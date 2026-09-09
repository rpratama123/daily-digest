from datetime import datetime

import pytest
from pydantic import ValidationError

from app.models import CollectorResult, Status


def test_collector_rejects_naive_observation_time() -> None:
    with pytest.raises(ValidationError):
        CollectorResult(name="example", status=Status.OK, observed_at=datetime(2026, 9, 9))
