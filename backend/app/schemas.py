from __future__ import annotations

from typing import Annotated, Any, Literal

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
    kind: Literal["op", "dc", "ac", "transient", "ccp", "global", "global_transient", "radial"]
    settings: dict[str, Any] = Field(default_factory=dict)


class CoaxPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parameters: dict[str, Any] = Field(default_factory=dict)


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


class DeleteCircuitTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=36)
    expected_revision: int = Field(ge=1, strict=True)


class DeleteCircuits(EmployeeRequest):
    model_config = ConfigDict(extra="forbid")
    circuits: list[DeleteCircuitTarget] = Field(min_length=1, max_length=100)

    @field_validator("circuits")
    @classmethod
    def distinct_circuits(cls, value: list[DeleteCircuitTarget]) -> list[DeleteCircuitTarget]:
        if len({target.id for target in value}) != len(value):
            raise ValueError("削除対象のモデルが重複しています")
        return value


class CreateRun(EmployeeRequest):
    circuit_id: str
    expected_revision: int = Field(ge=1)
    analysis: Analysis


class StudyAxis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=200)
    values: list[Annotated[float, Field(strict=True, allow_inf_nan=False)]] = Field(min_length=1, max_length=100)


class CreateStudy(CreateRun):
    name: str = Field(default="Parameter study", min_length=1, max_length=200)
    axes: list[StudyAxis] = Field(min_length=1, max_length=2)


class CompareRuns(BaseModel):
    run_ids: list[str] = Field(min_length=2, max_length=4)
    phase_align: bool = True


class CreateBenchmark(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    material: str = Field(min_length=1, max_length=200)
    measurement_definition: str = Field(min_length=1, max_length=5000)
    frequency_hz: float = Field(gt=0, allow_inf_nan=False)
    pressure_pa: float = Field(gt=0, allow_inf_nan=False)
    provenance: str | dict[str, Any]
    uncertainty: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    format: Literal["csv", "json"] = "json"
    data: str | dict[str, Any] | list[dict[str, Any]]


class CompareBenchmark(BaseModel):
    run_id: str


class ImportPackage(EmployeeRequest):
    package: dict[str, Any]
