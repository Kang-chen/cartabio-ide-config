from __future__ import annotations

import multiprocessing as mp
from pathlib import Path

from tusoai.optimization import _DMLogSinks, _dm_read_history_entries


def _append_history(history_path: str, worker_id: int, count: int) -> None:
    path = Path(history_path)
    sinks = _DMLogSinks(
        history_path=path,
        dev_path=path.with_name(f"dev_{worker_id}.json"),
        prompt_io_path=path.with_name(f"prompt_{worker_id}.json"),
        multi_machine=True,
        run_id=f"worker-{worker_id}",
    )
    for seq in range(count):
        sinks.log_history(
            {
                "stage": "evolve",
                "worker_id": worker_id,
                "worker_seq": seq,
                "accuracy": worker_id + seq / 1000.0,
                "code": f"def model_{worker_id}_{seq}():\n    return {seq}\n",
            }
        )


def test_multi_machine_history_writers_do_not_lose_or_corrupt_entries(tmp_path: Path) -> None:
    history_path = tmp_path / "history.json"
    history_path.write_text("[]", encoding="utf-8")

    worker_count = 4
    entries_per_worker = 12
    ctx = mp.get_context("spawn")
    processes = [
        ctx.Process(
            target=_append_history,
            args=(str(history_path), worker_id, entries_per_worker),
        )
        for worker_id in range(worker_count)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0

    entries = _dm_read_history_entries(history_path)
    assert len(entries) == worker_count * entries_per_worker
    assert {
        (entry["worker_id"], entry["worker_seq"])
        for entry in entries
    } == {
        (worker_id, seq)
        for worker_id in range(worker_count)
        for seq in range(entries_per_worker)
    }
    assert len({(entry["run_id"], entry["history_seq"]) for entry in entries}) == len(entries)
    assert not list(tmp_path.glob("history.json.lock.d*"))
