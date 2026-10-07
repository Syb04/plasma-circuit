from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Position(BaseModel):
    x: float = 0
    y: float = 0


class Component(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    kind: str = Field(min_length=1, max_length=20)
    label: str = Field(default="", max_length=100)
    ports: list[str] = Field(default_factory=list, max_length=64)
    parameters: dict[str, Any] = Field(default_factory=dict)
    position: Position = Field(default_factory=Position)
    rotation: int = 0


class Endpoint(BaseModel):
    component_id: str
    port: str


class Wire(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    source: Endpoint
    target: Endpoint


class SpiceModel(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    definition: str = Field(min_length=1, max_length=100000)


class CircuitDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    components: list[Component] = Field(default_factory=list, max_length=500)
    wires: list[Wire] = Field(default_factory=list, max_length=2000)
    parameters: dict[str, float] = Field(default_factory=dict)
    models: list[SpiceModel] = Field(default_factory=list, max_length=100)


class Analysis(BaseModel):
    kind: Literal["op", "dc", "ac", "transient", "ccp", "global"]
    settings: dict[str, Any] = Field(default_factory=dict)


class EmployeeRequest(BaseModel):
    employee_id: str = Field(min_length=1, max_length=80)

    @field_validator("employee_id")
    @classmethod
    def normalize_employee_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("社員番号を入力してください")
        return value


class SaveCircuit(EmployeeRequest):
    document: CircuitDocument


class UpdateCircuit(SaveCircuit):
    expected_revision: int = Field(ge=1)


class CreateRun(EmployeeRequest):
    circuit_id: str
    expected_revision: int = Field(ge=1)
    analysis: Analysis
