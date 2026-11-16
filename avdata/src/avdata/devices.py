"""Validated sensor settings and deterministic device/network policy."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DeviceType = Literal["microphone", "camera", "lidar", "gnss"]


class SensorConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sample_rate_hz: Literal[8000, 16000, 22050, 44100, 48000] | None = None
    gain_db: float | None = Field(default=None, ge=-24, le=36)
    noise_suppression: bool | None = None
    beamforming: bool | None = None
    camera_resolution: (
        Literal["640x480", "1280x720", "1920x1080", "3840x2160"] | None
    ) = None
    network_preference: Literal["auto", "wifi", "cellular"] | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set or any(
            getattr(self, field) is None for field in self.model_fields_set
        ):
            raise ValueError("provide at least one non-null configuration setting")
        return self

    def for_device(self, device_type: str) -> dict:
        values = self.model_dump(exclude_unset=True)
        audio = {"sample_rate_hz", "gain_db", "noise_suppression", "beamforming"}
        if device_type != "microphone" and audio & values.keys():
            raise ValueError("audio settings require a microphone")
        if device_type != "camera" and "camera_resolution" in values:
            raise ValueError("camera resolution requires a camera")
        return values


def default_configuration(device_type: str) -> dict:
    base = {"network_preference": "auto"}
    if device_type == "microphone":
        base.update(
            sample_rate_hz=16000,
            gain_db=12.0,
            noise_suppression=True,
            beamforming=False,
        )
    elif device_type == "camera":
        base["camera_resolution"] = "1920x1080"
    return base


def sensor_status(
    now: datetime,
    last_seen: datetime | None,
    gateway: str,
    health: str,
    syncing: bool,
    maintenance: bool,
    signal: int | None,
) -> str:
    if gateway == "SUSPENDED":
        return "SUSPENDED"
    if last_seen is None:
        return "NEVER_CONNECTED"
    age = (now - last_seen).total_seconds()
    if gateway in {"OFFLINE", "NEVER_CONNECTED"} or age > 90:
        return "OFFLINE"
    if (
        age > 30
        or gateway == "STALE"
        or health == "FAULT"
        or (signal is not None and signal < -85)
    ):
        return "DEGRADED"
    if maintenance:
        return "MAINTENANCE"
    return "SYNCING" if syncing else "ONLINE"


def network_policy(info: dict, preference: str = "auto") -> dict:
    """Recommend a link; never choose unauthenticated Wi-Fi."""
    wifi_ok = (
        info.get("wifi_authenticated") is True
        and info.get("wifi_signal_dbm", -120) > -70
    )
    cellular_ok = info.get("cellular_signal_dbm", -120) >= -85
    if preference != "cellular" and wifi_ok:
        network, action = "wifi", "Upload stored media through authenticated Wi-Fi"
    elif cellular_ok:
        network, action = "cellular", "Transmit audio features and alerts"
    else:
        network, action = "offline", "Record locally, buffer alerts, and retry later"
    return {"recommended_network": network, "action": action}


def audio_policy(configuration: dict, emergency: bool, clipping_seconds: float) -> dict:
    """Effective audio settings; the user's desired values stay unchanged."""
    effective = dict(configuration)
    effective["sample_rate_hz"] = (
        max(44100, configuration.get("sample_rate_hz", 16000))
        if emergency
        else configuration.get("sample_rate_hz", 16000)
    )
    if clipping_seconds > 2:
        effective["gain_db"] = max(-24, configuration.get("gain_db", 12.0) - 6)
    return effective
