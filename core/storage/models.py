from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MetricReading:
    source: str
    metric_type: str
    timestamp: datetime
    value: float
    unit: str
