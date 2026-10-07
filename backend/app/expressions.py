"""Validated equation expressions and engineering values for SPICE generation.

Equations are parsed as a restricted AST, never evaluated as Python. Variable
targets must be a single SPICE voltage/current probe or ``time``. Intermediate
and parameter expressions are recursively expanded before emitting netlist text.
"""

from __future__ import annotations

import ast
import math
import re
from collections.abc import Mapping


class ExpressionError(ValueError):
    """An equation or engineering value cannot be used safely."""


MAX_EXPRESSION_LENGTH = 4096
MAX_AST_NODES = 512
MAX_DEPTH = 48
MAX_OUTPUT_LENGTH = 65536
MAX_SYMBOLS = 256

_NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")
_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_VALUE = re.compile(rf"\s*({_NUMBER})\s*([A-Za-zµμΩ]*)\s*\Z")
_PREFIXES = {
    "": 1.0,
    "T": 1e12,
    "G": 1e9,
    "M": 1e6,
    "meg": 1e6,
    "k": 1e3,
    "m": 1e-3,
    "u": 1e-6,
    "µ": 1e-6,
    "μ": 1e-6,
    "n": 1e-9,
    "p": 1e-12,
    "f": 1e-15,
}
# Unit labels do not convert quantities between dimensions. eV remains eV, for
# example; this parser is for circuit values, rather than a physical unit system.
_UNITS = frozenset({"V", "A", "F", "H", "Hz", "s", "Ohm", "ohm", "Ω", "W", "C", "K", "eV", "Pa"})
_PROBE = re.compile(
    r"(?:[vV]\([A-Za-z0-9_.:+#-]+(?:,[A-Za-z0-9_.:+#-]+)?\)"
    r"|[iI]\([A-Za-z0-9_.:+#-]+\)|time)\Z"
)
_UNARY_FUNCTIONS = frozenset(
    {"exp", "log", "ln", "log10", "sqrt", "abs", "sin", "cos", "tan",
     "asin", "acos", "atan", "sinh", "cosh", "tanh"}
)
_FUNCTIONS = _UNARY_FUNCTIONS | {"pow", "min", "max", "limit", "if"}
_RESERVED = _FUNCTIONS | {"pi", "e", "t", "time", "__conditional__"}


def parse_si(value: float | int | str) -> float:
    """Parse a finite scalar with an optional engineering prefix/unit label.

    Prefix case matters: ``M``/``meg`` is mega and ``m`` is milli. Known units
    may follow the prefix (``40 MHz``, ``2.2kOhm``). Arbitrary trailing text,
    ambiguous uppercase ``K`` as kilo, expressions and nonfinite values fail.
    An isolated ``K`` is the kelvin unit and therefore has multiplier one.
    """
    if isinstance(value, bool):
        raise ExpressionError("Boolean values are not numeric circuit values")
    if isinstance(value, (int, float)):
        try:
            result = float(value)
        except (OverflowError, ValueError) as exc:
            raise ExpressionError("Numeric value is out of range") from exc
    elif isinstance(value, str):
        if len(value) > 128:
            raise ExpressionError("Engineering value is too long")
        match = _VALUE.fullmatch(value)
        if not match:
            raise ExpressionError(f"Invalid engineering value: {value!r}")
        number, suffix = match.groups()
        if suffix in _PREFIXES:
            multiplier = _PREFIXES[suffix]
        elif suffix in _UNITS:
            multiplier = 1.0
        else:
            matches = [
                multiplier
                for prefix, multiplier in _PREFIXES.items()
                if prefix and suffix.startswith(prefix) and suffix[len(prefix):] in _UNITS
            ]
            if len(matches) != 1:
                raise ExpressionError(f"Unknown or ambiguous engineering suffix: {suffix!r}")
            multiplier = matches[0]
        result = float(number) * multiplier
    else:
        raise ExpressionError("Expected a number or an engineering value string")
    if not math.isfinite(result):
        raise ExpressionError("Numeric value must be finite")
    return result


def _number(value: float | int) -> str:
    return format(parse_si(value), ".17g")


def _symbols(values: Mapping | None, label: str) -> dict:
    if values is None:
        return {}
    if not isinstance(values, Mapping):
        raise ExpressionError(f"{label} must be a symbol mapping")
    if len(values) > MAX_SYMBOLS:
        raise ExpressionError(f"Too many {label}")
    result = dict(values)
    for name in result:
        if not isinstance(name, str) or not _NAME.fullmatch(name) or name in _RESERVED:
            raise ExpressionError(f"Invalid or reserved symbol name: {name!r}")
    return result


class _Compiler:
    def __init__(self, variables: Mapping | None, parameters: Mapping | None, intermediates: Mapping | None):
        self.variables = _symbols(variables, "variables")
        self.parameters = _symbols(parameters, "parameters")
        self.intermediates = _symbols(intermediates, "intermediates")
        groups = (set(self.variables), set(self.parameters), set(self.intermediates))
        if groups[0] & groups[1] or groups[0] & groups[2] or groups[1] & groups[2]:
            raise ExpressionError("Variables, parameters and intermediates must have distinct names")
        if sum(map(len, groups)) > MAX_SYMBOLS:
            raise ExpressionError("Too many equation symbols")
        for name, target in self.variables.items():
            if not isinstance(target, str) or not _PROBE.fullmatch(target):
                raise ExpressionError(f"Variable {name!r} must map to a SPICE probe or time")
        for name, expression in self.intermediates.items():
            if not isinstance(expression, str):
                raise ExpressionError(f"Intermediate {name!r} must be an expression string")
        self.active: list[str] = []
        self.emission_depth = 0
        self.cache: dict[str, str] = {}
        self.constant_cache: dict[str, float | None] = {}

    def parse(self, expression: str) -> ast.AST:
        if not isinstance(expression, str) or not expression.strip():
            raise ExpressionError("Equation must be a nonempty string")
        if len(expression) > MAX_EXPRESSION_LENGTH:
            raise ExpressionError("Equation is too long")
        # QUCS power has mathematical precedence; Python's bitwise XOR does not.
        source = expression.replace("^", "**")
        source = re.sub(r"\bif\s*(?=\()", "__conditional__", source)
        source = source.replace("&&", " and ").replace("||", " or ")
        try:
            tree = ast.parse(source.strip(), mode="eval")
        except (SyntaxError, RecursionError, ValueError) as exc:
            raise ExpressionError("Invalid equation syntax") from exc
        stack = [(tree, 0)]
        count = 0
        while stack:
            node, depth = stack.pop()
            count += 1
            if count > MAX_AST_NODES or depth > MAX_DEPTH:
                raise ExpressionError("Equation is too complex")
            stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
        return tree.body

    def compile(self, expression: str) -> str:
        return self.emit(self.parse(expression))

    def constant_value(self, node: ast.AST, depth: int = 0, active: tuple[str, ...] = ()) -> float | None:
        """Resolve numeric exponents using only explicitly supported arithmetic.

        This is constant folding of an already validated AST, not Python eval.
        Dynamic symbols/functions return None rather than assuming integrality.
        """
        if depth > MAX_DEPTH:
            return None
        if isinstance(node, ast.Constant) and not isinstance(node.value, bool) and isinstance(node.value, (int, float)):
            return parse_si(node.value)
        if isinstance(node, ast.Name):
            if node.id == "pi":
                return math.pi
            if node.id == "e":
                return math.e
            if node.id in self.constant_cache:
                return self.constant_cache[node.id]
            if node.id in active:
                return None
            if node.id in self.parameters:
                value = self.parameters[node.id]
                try:
                    result = parse_si(value)
                except ExpressionError:
                    result = self.constant_value(self.parse(value), depth + 1, (*active, node.id)) if isinstance(value, str) else None
            elif node.id in self.intermediates:
                result = self.constant_value(self.parse(self.intermediates[node.id]), depth + 1, (*active, node.id))
            else:
                return None
            self.constant_cache[node.id] = result
            return result
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = self.constant_value(node.operand, depth + 1, active)
            return None if value is None else -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp):
            left = self.constant_value(node.left, depth + 1, active)
            right = self.constant_value(node.right, depth + 1, active)
            if left is None or right is None:
                return None
            try:
                if isinstance(node.op, ast.Add):
                    value = left + right
                elif isinstance(node.op, ast.Sub):
                    value = left - right
                elif isinstance(node.op, ast.Mult):
                    value = left * right
                elif isinstance(node.op, ast.Div):
                    value = left / right
                elif isinstance(node.op, ast.Pow):
                    value = math.pow(left, right)
                else:
                    return None
                return value if math.isfinite(value) else None
            except (ValueError, OverflowError, ZeroDivisionError):
                return None
        return None

    def power(self, base_node: ast.AST, exponent_node: ast.AST, base: str, exponent: str) -> str:
        exponent_value = self.constant_value(exponent_node)
        # ngspice pow() uses abs(base); pwr() restores the sign for *all*
        # exponents. Therefore pwr() is correct only for odd integer powers.
        if exponent_value is not None and exponent_value.is_integer():
            function = "pwr" if int(exponent_value) % 2 else "pow"
            return f"{function}({base},{exponent})"
        base_value = self.constant_value(base_node)
        if base_value is not None and base_value < 0:
            raise ExpressionError("Negative bases require a known integer exponent")
        return f"pow({base},{exponent})"

    def resolve(self, name: str) -> str:
        if name == "pi":
            return _number(math.pi)
        if name == "e":
            return _number(math.e)
        if name in {"t", "time"}:
            return "time"
        if name in self.variables:
            return self.variables[name]
        if name in self.cache:
            return self.cache[name]
        if name not in self.parameters and name not in self.intermediates:
            raise ExpressionError(f"Unknown equation symbol: {name!r}")
        if name in self.active:
            cycle = " -> ".join([*self.active, name])
            raise ExpressionError(f"Cyclic equation definitions: {cycle}")
        if len(self.active) >= MAX_DEPTH:
            raise ExpressionError("Equation definitions are nested too deeply")
        self.active.append(name)
        try:
            if name in self.parameters:
                value = self.parameters[name]
                if isinstance(value, str):
                    try:
                        result = _number(parse_si(value))
                    except ExpressionError:
                        result = self.compile(value)
                else:
                    result = _number(value)
            else:
                result = self.compile(self.intermediates[name])
            self.cache[name] = f"({result})"
            return self.cache[name]
        finally:
            self.active.pop()

    def emit(self, node: ast.AST) -> str:
        self.emission_depth += 1
        try:
            if self.emission_depth > MAX_DEPTH * 2:
                raise ExpressionError("Equation definitions are nested too deeply")
            result = self._emit(node)
            if len(result) > MAX_OUTPUT_LENGTH:
                raise ExpressionError("Expanded equation is too long")
            return result
        finally:
            self.emission_depth -= 1

    def _emit(self, node: ast.AST) -> str:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ExpressionError("Only numeric literals are allowed")
            return _number(node.value)
        if isinstance(node, ast.Name):
            return self.resolve(node.id)
        if isinstance(node, ast.BinOp):
            left, right = self.emit(node.left), self.emit(node.right)
            if isinstance(node.op, ast.Pow):
                return self.power(node.left, node.right, left, right)
            operators = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}
            operator = operators.get(type(node.op))
            if operator is None:
                raise ExpressionError("Unsupported equation operator")
            return f"({left}{operator}{right})"
        if isinstance(node, ast.UnaryOp):
            operators = {ast.UAdd: "+", ast.USub: "-", ast.Not: "!"}
            operator = operators.get(type(node.op))
            if operator is None:
                raise ExpressionError("Unsupported unary operator")
            return f"({operator}{self.emit(node.operand)})"
        if isinstance(node, ast.Compare):
            operators = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==", ast.NotEq: "!="}
            operands = [node.left, *node.comparators]
            terms = []
            for index, operator_node in enumerate(node.ops):
                operator = operators.get(type(operator_node))
                if operator is None:
                    raise ExpressionError("Unsupported comparison")
                terms.append(f"({self.emit(operands[index])}{operator}{self.emit(operands[index + 1])})")
            return "(" + "&&".join(terms) + ")"
        if isinstance(node, ast.BoolOp):
            operator = "&&" if isinstance(node.op, ast.And) else "||"
            return "(" + operator.join(self.emit(value) for value in node.values) + ")"
        if isinstance(node, ast.IfExp):
            return f"({self.emit(node.test)}?{self.emit(node.body)}:{self.emit(node.orelse)})"
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.keywords:
                raise ExpressionError("Only named mathematical functions with positional arguments are allowed")
            function = node.func.id
            if function == "__conditional__":
                function = "if"
            if function not in _FUNCTIONS:
                raise ExpressionError(f"Unsupported mathematical function: {function!r}")
            arguments = [self.emit(argument) for argument in node.args]
            expected = 1 if function in _UNARY_FUNCTIONS else 2 if function == "pow" else 3
            if function in {"min", "max"}:
                if not 2 <= len(arguments) <= 16:
                    raise ExpressionError(f"{function} requires 2 to 16 arguments")
                result = arguments[0]
                for argument in arguments[1:]:
                    result = f"{function}({result},{argument})"
                return result
            if len(arguments) != expected:
                raise ExpressionError(f"{function} requires {expected} arguments")
            if function == "pow":
                return self.power(node.args[0], node.args[1], arguments[0], arguments[1])
            if function == "if":
                condition, true_value, false_value = arguments
                return f"({condition}?{true_value}:{false_value})"
            if function == "limit":
                value, minimum, maximum = arguments
                return f"min(max({value},{minimum}),{maximum})"
            if function == "log":
                function = "ln"
            return f"{function}({','.join(arguments)})"
        raise ExpressionError(f"Unsupported equation syntax: {type(node).__name__}")


def compile_expression(
    expression: str,
    variables: dict[str, str] | None = None,
    parameters: dict[str, float | str] | None = None,
    intermediates: dict[str, str] | None = None,
) -> str:
    """Translate a restricted QUCS-style equation into an ngspice expression.

    Examples: ``V1`` maps to ``V(anode,cathode)``, ``I1`` maps to a hidden
    current-state voltage, and ``if(V1 > 0, V1^2, 0)`` becomes a SPICE
    conditional. Parameter strings can be engineering values or expressions.
    Known integer exponents preserve negative-base signs, including numeric
    parameters and arithmetic intermediates. Fractional or dynamic exponents
    require nonnegative bases: ngspice uses abs(base) for pow(), so callers must
    ensure that domain for time-varying values (or explicitly write abs(base)).
    Known negative constant bases with noninteger/dynamic exponents are rejected.
    Unsupported syntax, unknown symbols, cycles and excessive complexity raise
    :class:`ExpressionError` before any simulator is invoked.
    """
    try:
        return _Compiler(variables, parameters, intermediates).compile(expression)
    except RecursionError as exc:
        raise ExpressionError("Equation definitions are nested too deeply") from exc
