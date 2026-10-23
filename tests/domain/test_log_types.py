import pickle

from log_analyzer.domain.log_types import MatchedLogs
from log_analyzer.domain.parser import MatchedLogsStore


def test_matched_logs_protocol_accepts_list_and_store(tmp_path):
    store_path = tmp_path / "matched-logs.pkl"
    with store_path.open("wb") as file_handle:
        pickle.dump({"level": "INFO", "message": "ready"}, file_handle)
    store = MatchedLogsStore(str(store_path), 1)

    assert isinstance([{"level": "INFO", "message": "ready"}], MatchedLogs)
    assert isinstance(store, MatchedLogs)
