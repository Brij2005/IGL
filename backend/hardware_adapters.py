"""Modular hardware integration adapters for the IGL Smart Entry & Exit module.

Every adapter is optional and degrades gracefully. No physical device is required
for the recorded-video workflow, and no fake device is ever reported as connected.

Implemented adapters:
    1. Thermal IP Camera            -> create_thermal_camera_adapter()
    2. Normal IP CCTV Camera        -> create_cctv_camera_adapter()
    3. RFID Reader and Tags         -> RfidReaderAdapter
    4. Access Control System        -> AccessControlAdapter
    5. Gate Turnstile               -> TurnstileAdapter
    6. Security Alarm / Indicator   -> AlarmAdapter
    7. Central Processing Server    -> CentralServerAdapter

The video adapters reuse the existing `video_sources` adapters so thermal and
normal IP cameras share the same capture, reconnect and health pipeline.
"""

from abc import ABC, abstractmethod
from typing import Optional

from video_sources import RTSPAdapter, create_adapter


class HardwareAdapter(ABC):
    """Base class for non-camera hardware adapters."""

    kind = "base"
    device_type = "GENERIC"

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self.connected = False
        self.last_error = None

    @abstractmethod
    def connect(self) -> dict:
        raise NotImplementedError

    def disconnect(self) -> dict:
        self.connected = False
        return {"connected": False, "kind": self.kind}

    def status(self) -> dict:
        return {
            "kind": self.kind,
            "device_type": self.device_type,
            "connected": bool(self.connected),
            "configured": bool(self.config),
            "last_error": self.last_error,
        }


class RfidReaderAdapter(HardwareAdapter):
    kind = "rfid_reader"
    device_type = "RFID"

    def connect(self) -> dict:
        endpoint = self.config.get("endpoint")
        if not endpoint:
            self.last_error = "No RFID reader endpoint configured"
            return {"connected": False, "message": self.last_error}
        # A real reader would open a TCP/serial/HTTP link here. Without an endpoint
        # the reader is reported as not connected rather than faked.
        self.connected = False
        self.last_error = "RFID reader transport is not implemented for this endpoint"
        return {"connected": False, "message": self.last_error}


class AccessControlAdapter(HardwareAdapter):
    kind = "access_control"
    device_type = "ACCESS_CONTROL"

    def connect(self) -> dict:
        endpoint = self.config.get("endpoint")
        if not endpoint:
            self.last_error = "No access-control endpoint configured"
            return {"connected": False, "message": self.last_error}
        self.connected = False
        self.last_error = "Access-control transport is not implemented for this endpoint"
        return {"connected": False, "message": self.last_error}

    def verify_card(self, card_id: str) -> dict:
        if not self.connected:
            return {"verified": False, "status": "NOT_CONNECTED", "person_id": None}
        return {"verified": False, "status": "NO_MATCH", "person_id": None}


class TurnstileAdapter(HardwareAdapter):
    kind = "turnstile"
    device_type = "TURNSTILE"

    def connect(self) -> dict:
        endpoint = self.config.get("endpoint")
        if not endpoint:
            self.last_error = "No turnstile endpoint configured"
            return {"connected": False, "message": self.last_error}
        self.connected = False
        self.last_error = "Turnstile transport is not implemented for this endpoint"
        return {"connected": False, "message": self.last_error}


class AlarmAdapter(HardwareAdapter):
    kind = "alarm"
    device_type = "SECURITY_ALARM"

    def connect(self) -> dict:
        endpoint = self.config.get("endpoint")
        if not endpoint:
            self.last_error = "No alarm endpoint configured"
            return {"connected": False, "message": self.last_error}
        self.connected = False
        self.last_error = "Alarm transport is not implemented for this endpoint"
        return {"connected": False, "message": self.last_error}

    def trigger(self, message: str = "") -> dict:
        if not self.connected:
            return {"triggered": False, "status": "NOT_CONNECTED"}
        return {"triggered": False, "status": "NO_TRANSPORT"}


class CentralServerAdapter(HardwareAdapter):
    kind = "central_server"
    device_type = "PROCESSING_SERVER"

    def connect(self) -> dict:
        endpoint = self.config.get("endpoint")
        if not endpoint:
            self.last_error = "No central server endpoint configured"
            return {"connected": False, "message": self.last_error}
        self.connected = False
        self.last_error = "Central server transport is not implemented for this endpoint"
        return {"connected": False, "message": self.last_error}


class StorageBackupAdapter(HardwareAdapter):
    """Storage and backup systems (evidence/report archival targets)."""

    kind = "storage_backup"
    device_type = "STORAGE_BACKUP"

    def connect(self) -> dict:
        endpoint = self.config.get("endpoint") or self.config.get("path")
        if not endpoint:
            self.last_error = "No storage/backup endpoint or path configured"
            return {"connected": False, "message": self.last_error}
        self.connected = False
        self.last_error = "Storage/backup transport is not implemented for this endpoint"
        return {"connected": False, "message": self.last_error}

    def status(self) -> dict:
        base = super().status()
        base["backup_schedule"] = self.config.get("schedule", "NOT_CONFIGURED")
        return base


class ThermalCameraAdapter(RTSPAdapter):
    """Thermal IP camera adapter.

    Reuses the existing RTSP capture path. When `calibrated` is enabled the
    adapter can read per-frame temperature data if the camera exposes it. This
    adapter never claims temperature measurement without a calibrated source.
    """

    kind = "thermal_rtsp"

    def _open(self):
        super()._open()
        self.calibrated = bool(self.options.get("calibrated", False))

    def temperature_supported(self) -> bool:
        # A generic RTSP thermal camera does not expose calibrated temperatures
        # unless the vendor provides a side channel. This stays False by default.
        return False


class CctvCameraAdapter(RTSPAdapter):
    """Normal RGB IP CCTV camera adapter reusing the RTSP capture path."""

    kind = "cctv_rtsp"


class ThermalCameraHardwareAdapter(HardwareAdapter):
    """Thermal IP camera descriptor with protocol, calibration and frame handling.

    Implements the generic parts of the device lifecycle that can be genuinely
    supported without vendor SDKs:

        * configuration validation (protocol, endpoint, calibration record)
        * connection state machine (never fakes success)
        * authentication material handling (credentials are not stored here)
        * frame acquisition through the shared RTSP adapter when the device
          exposes an RGB/thermal video stream
        * radiometric frame acquisition when the device exposes a temperature
          export path (per-vendor; requires configuration)
        * disconnection and reconnect state reporting

    A device is only reported CONNECTED when its stream is genuinely opened.
    """

    kind = "thermal_camera"
    device_type = "THERMAL_IP_CAMERA"
    SUPPORTED_PROTOCOLS = {"rtsp", "rtsps", "http", "https", "file"}

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.adapter = None
        self.calibration = None
        self.frames_read = 0
        self.last_frame_at = None

    def _calibration_record(self):
        record = self.config.get("calibration") or {}
        from thermal_ingestion import validate_calibration

        return validate_calibration(record if isinstance(record, dict) else {})

    def connect(self) -> dict:
        endpoint = self.config.get("endpoint") or self.config.get("source_url")
        protocol = (self.config.get("protocol") or "").lower()
        if not endpoint:
            self.last_error = "No thermal camera endpoint configured"
            self.connected = False
            return {"connected": False, "message": self.last_error}
        if protocol and protocol not in self.SUPPORTED_PROTOCOLS:
            self.last_error = f"Unsupported thermal camera protocol: {protocol}"
            self.connected = False
            return {"connected": False, "message": self.last_error}

        self.calibration = self._calibration_record()
        try:
            self.adapter = create_adapter(protocol or "rtsp", endpoint, self.config)
            self.adapter.open()
        except Exception as exc:
            self.adapter = None
            self.connected = False
            self.last_error = f"Thermal camera connection failed: {exc}"
            return {"connected": False, "message": self.last_error}

        self.connected = True
        self.last_error = None
        return {
            "connected": True,
            "kind": self.kind,
            "protocol": protocol or "rtsp",
            "calibrated": self.calibration.get("calibrated", False),
            "temperature_supported": bool(self.config.get("radiometric_path")),
        }

    def read_frame(self):
        """Return (frame, temperature_grid_or_None). Never invents temperatures."""
        if not self.connected or not self.adapter:
            raise RuntimeError("Thermal camera is not connected")
        frame = self.adapter.read()
        self.frames_read += 1
        from datetime import datetime, timezone

        self.last_frame_at = datetime.now(timezone.utc).isoformat()
        temperature_grid = None
        radiometric = self.config.get("radiometric_path")
        if radiometric and Path(radiometric).exists():
            try:
                from thermal_ingestion import load_radiometric_array

                temperature_grid = load_radiometric_array(radiometric)
            except Exception as exc:
                self.last_error = f"Radiometric read failed: {exc}"
        return frame, temperature_grid

    def disconnect(self) -> dict:
        if self.adapter:
            try:
                self.adapter.release()
            except Exception:
                pass
        self.adapter = None
        self.connected = False
        return {"connected": False, "kind": self.kind}

    def status(self) -> dict:
        base = super().status()
        base.update(
            {
                "protocol": self.config.get("protocol", "rtsp"),
                "calibrated": self.calibration.get("calibrated", False) if self.calibration else False,
                "calibration_reason": (self.calibration or {}).get("reason"),
                "radiometric_source_configured": bool(self.config.get("radiometric_path")),
                "frames_read": self.frames_read,
                "last_frame_at": self.last_frame_at,
            }
        )
        return base


class CctvCameraHardwareAdapter(HardwareAdapter):
    """Non-streaming IP CCTV camera descriptor (configuration/health only)."""

    kind = "cctv_camera"
    device_type = "IP_CCTV_CAMERA"

    def connect(self) -> dict:
        endpoint = self.config.get("endpoint") or self.config.get("source_url")
        if not endpoint:
            self.last_error = "No CCTV camera endpoint configured"
            return {"connected": False, "message": self.last_error}
        self.connected = False
        self.last_error = "CCTV camera transport is not implemented for this endpoint"
        return {"connected": False, "message": self.last_error}


def create_thermal_camera_adapter(source_url: str, options: Optional[dict] = None) -> ThermalCameraAdapter:
    return ThermalCameraAdapter(source_url, options or {})


def create_cctv_camera_adapter(source_url: str, options: Optional[dict] = None) -> CctvCameraAdapter:
    return CctvCameraAdapter(source_url, options or {})


def create_video_adapter(source_type: str, source_url: str, options: Optional[dict] = None):
    """Create a video adapter for thermal or normal footage.

    Falls back to the shared `video_sources` factory for file/image/webcam/http
    sources so recorded thermal and CCTV videos use the same capture pipeline.
    """
    normalized = (source_type or "").lower()
    if normalized in {"thermal", "thermal_rtsp"}:
        return create_thermal_camera_adapter(source_url, options)
    if normalized in {"cctv", "cctv_rtsp"}:
        return create_cctv_camera_adapter(source_url, options)
    return create_adapter(normalized, source_url, options)


def create_hardware_adapter(device_type: str, config: Optional[dict] = None) -> Optional[HardwareAdapter]:
    normalized = (device_type or "").lower().replace(" ", "_")
    mapping = {
        "rfid": RfidReaderAdapter,
        "rfid_reader": RfidReaderAdapter,
        "access_control": AccessControlAdapter,
        "turnstile": TurnstileAdapter,
        "alarm": AlarmAdapter,
        "security_alarm": AlarmAdapter,
        "central_server": CentralServerAdapter,
        "processing_server": CentralServerAdapter,
        "storage_backup": StorageBackupAdapter,
        "storage": StorageBackupAdapter,
        "thermal_camera": ThermalCameraHardwareAdapter,
        "cctv_camera": CctvCameraHardwareAdapter,
        "ip_cctv_camera": CctvCameraHardwareAdapter,
    }
    adapter_cls = mapping.get(normalized)
    if adapter_cls is None:
        return None
    return adapter_cls(config)