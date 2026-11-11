"""The message envelope every vehicle message uses, and the telemetry payload."""

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

SCHEMA_VERSION = "sv.v1"

Channel = Literal["telemetry", "heartbeat", "event", "disconnect", "cmd-ack"]


class Envelope(BaseModel):
    """{schema, msg_id, tenant_id, vehicle_id, run_id, ts, channel, data}.

    The IoT rule will later add client_id and rx_ts; they are optional here.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    schema_version: str = Field(alias="schema", default=SCHEMA_VERSION)
    msg_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    vehicle_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    ts: AwareDatetime
    channel: Channel
    data: dict = Field(default_factory=dict)
    client_id: str | None = None
    rx_ts: datetime | None = None

    def to_message(self) -> dict:
        """JSON-ready dict, as it would travel over MQTT and SQS."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class TelemetryData(BaseModel):
    """The data of a telemetry message, sent at 2 Hz."""

    speed_kmh: float
    x: float
    y: float
    heading_deg: float
    throttle: float = Field(ge=0, le=1)
    brake: float = Field(ge=0, le=1)
    steer: float = Field(ge=-1, le=1)


TELEMETRY_FIELDS = tuple(TelemetryData.model_fields)


class EventData(BaseModel):
    """The data of an event message, such as a collision. Extra fields are kept."""

    model_config = ConfigDict(extra="allow")

    type: str = Field(min_length=1)
    severity: Literal["low", "medium", "high"] = "low"
