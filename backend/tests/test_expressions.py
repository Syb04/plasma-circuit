"""Equation safety is part of the boundary between user data and netlists."""

import math

import pytest

from app.expressions import ExpressionError, compile_expression, parse_si


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (2, 2), (0.25, 0.25), ("1e-3", 0.001), ("10m", 0.01),
        ("250 V", 250), ("40 MHz", 40e6), ("2.2kOhm", 2200),
        ("1meg", 1e6), ("1M", 1e6), ("1m", 0.001),
        ("3uF", 3e-6), ("3µF", 3e-6), ("3μF", 3e-6),
        ("5n", 5e-9), ("5p", 5e-12), ("5f", 5e-15),
        ("-2.5mA", -0.0025), ("300 K", 300), ("1 eV", 1),
    ],
)
def test_parse_engineering_values(value, expected):
    assert parse_si(value) == pytest.approx(expected)


@pytest.mark.parametrize("value", [True, None, [], "", "nan", "inf", "1e999", float("inf"), "1 kg", "2.2foobar", "40 MHzz", "1 k V", "1;quit", "1+2", "1,000"])
def test_reject_bad_engineering_values(value):
    with pytest.raises(ExpressionError):
        parse_si(value)


def test_power_has_mathematical_precedence_and_probes_are_substituted():
    result = compile_expression("-V1^2 + V1*V2 + I1", {"V1": "V(a,b)", "V2": "V(c,0)", "I1": "V(eddy_current)"})
    assert result == "(((-pow(V(a,b),2))+(V(a,b)*V(c,0)))+V(eddy_current))"


@pytest.mark.parametrize("exponent,function", [(3, "pwr"), (2, "pow"), (-3, "pwr"), (-2, "pow"), (0, "pow"), (3.0, "pwr")])
def test_integer_powers_preserve_negative_base_sign(exponent, function):
    result = compile_expression(f"V1^({exponent})", {"V1": "V(a)"})
    assert result.startswith(f"{function}(V(a),")
    called = compile_expression(f"pow(V1,{exponent})", {"V1": "V(a)"})
    assert called.startswith(f"{function}(V(a),")


@pytest.mark.parametrize("value", [3, 3.0, "3", "3e0", "1+2"])
def test_parameter_integer_power_preserves_negative_base_sign(value):
    assert compile_expression("V1^exponent", {"V1": "V(a)"}, {"exponent": value}).startswith("pwr(V(a),")


def test_intermediate_integer_exponent():
    result = compile_expression("V1^exponent", {"V1": "V(a)"}, {"a": "2"}, {"exponent": "a+1"})
    assert result.startswith("pwr(V(a),")
    assert compile_expression("V1^(exponent+1)", {"V1": "V(a)"}, {"exponent": 3}).startswith("pow(V(a),")


@pytest.mark.parametrize("expression", ["(-2)^0.5", "pow(-2,0.5)", "(-2)^time"])
def test_known_negative_base_rejects_noninteger_or_dynamic_exponent(expression):
    with pytest.raises(ExpressionError, match="integer exponent"):
        compile_expression(expression)


def test_fractional_power_has_explicit_nonnegative_domain():
    assert compile_expression("abs(V1)^0.5", {"V1": "V(a)"}) == "pow(abs(V(a)),0.5)"


def test_intermediates_parameters_and_time():
    result = compile_expression(
        "conductance*V1 + sin(2*pi*frequency*t) + time",
        variables={"V1": "V(p,n)"},
        parameters={"resistance": "1kOhm", "frequency": "40 MHz", "scale": "2*resistance"},
        intermediates={"conductance": "1/scale"},
    )
    assert "V(p,n)" in result
    assert "40000000" in result
    assert "1000" in result
    assert result.count("time") == 2
    assert not any(name in result for name in ["conductance", "resistance", "frequency", "scale"])


def test_multiple_parameters_and_no_partial_symbol_replacement():
    assert compile_expression("V1+V10", {"V1": "V(a)", "V10": "V(b)"}) == "(V(a)+V(b))"
    assert compile_expression("e", parameters={"unused": 2}) == format(math.e, ".17g")


def test_conditionals_and_clamping():
    assert compile_expression("if(V1 > 0, V1, 0)", {"V1": "V(a)"}) == "(((V(a)>0))?V(a):0)"
    assert compile_expression("limit(V1,-1,1)", {"V1": "V(a)"}) == "min(max(V(a),(-1)),1)"
    assert compile_expression("min(1,2,3)") == "min(min(1,2),3)"
    assert "&&" in compile_expression("if(0<V1<1,1,0)", {"V1": "V(a)"})


@pytest.mark.parametrize("expression", [
    "__import__('os').system('id')", "V1.__class__", "V1[0]", "[1,2]",
    "(lambda: 1)()", "sum([1])", "open('x')", "1;quit", "'text'",
    "True", "1//2", "1%2", "sqrt(value=2)", "sqrt(*[1])", "unknown",
    "max(1)", "pow(2)", "if(1,2)", "1e999", "1 << 3",
    "{1: 2}", "(x for x in [1])", "(x:=1)", "V1 is V1",
])
def test_reject_unsupported_or_unsafe_equations(expression):
    with pytest.raises(ExpressionError):
        compile_expression(expression, {"V1": "V(a)"})


@pytest.mark.parametrize("target", ["V(a);quit", "V(a) + 1", "V(a\nb)", "exec(id)", "V(a){1}", "V(a,b,c)"])
def test_probe_mapping_cannot_inject_netlist_text(target):
    with pytest.raises(ExpressionError):
        compile_expression("V1", {"V1": target})


def test_cycles_in_parameters_or_intermediates():
    with pytest.raises(ExpressionError, match="Cyclic"):
        compile_expression("a", parameters={"a": "b+1", "b": "a-1"})
    with pytest.raises(ExpressionError, match="Cyclic"):
        compile_expression("a", parameters={"a": "b"}, intermediates={"b": "a"})


def test_symbol_collision_and_reserved_names():
    with pytest.raises(ExpressionError, match="distinct"):
        compile_expression("a", variables={"a": "V(a)"}, parameters={"a": 1})
    with pytest.raises(ExpressionError, match="reserved"):
        compile_expression("pi", parameters={"pi": 3})
    with pytest.raises(ExpressionError):
        compile_expression("a", intermediates={"a": 2})


def test_complexity_limits():
    with pytest.raises(ExpressionError):
        compile_expression("1+" * 2500 + "1")
    with pytest.raises(ExpressionError):
        compile_expression("(" * 400 + "1" + ")" * 400)
    definitions = {f"x{index}": f"x{index+1}+x{index+1}" for index in range(25)}
    definitions["x25"] = "1"
    with pytest.raises(ExpressionError, match="too long"):
        compile_expression("x0", intermediates=definitions)
