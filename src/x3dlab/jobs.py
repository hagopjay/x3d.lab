"""Background jobs started from the dashboard or a notebook cell.

A request never waits for a model: POST /api/jobs answers 202 with a job id at once, the work runs in a
thread, and progress arrives on the event stream. That keeps every request far under Cloudflare's
~100-second limit, which a full write-and-repair run can exceed.

Spend guards: at most `max_llm_jobs` model-using jobs per session and one at a time.
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from .events import bus


class JobRefused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class Job:
    id: str
    kind: str
    params: dict
    uses_llm: bool
    status: str = "queued"       # queued | running | done | error
    result: dict | None = None
    error: str | None = None
    created: float = field(default_factory=time.time)
    started: float | None = None
    ended: float | None = None

    def public(self) -> dict:
        result = {k: v for k, v in self.result.items() if not k.startswith("_")} if isinstance(self.result, dict) else self.result
        return {"id": self.id, "kind": self.kind, "status": self.status, "params": self.params, "result": result,
                "error": self.error, "created": self.created, "started": self.started, "ended": self.ended}


class Jobs:
    def __init__(self, max_llm_jobs: int = 30, workers: int = 2):
        self.max_llm_jobs = max_llm_jobs
        self.llm_jobs_started = 0
        self._handlers: dict[str, tuple[Callable[[Job], dict], bool]] = {}
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="x3dlab-job")
        self._llm_running = 0

    def register(self, kind: str, fn: Callable[[Job], dict], uses_llm: bool | Callable[[dict], bool] = False) -> None:
        self._handlers[kind] = (fn, uses_llm)

    def kinds(self) -> list[str]:
        return sorted(self._handlers)

    def submit(self, kind: str, params: dict | None = None) -> Job:
        if kind not in self._handlers:
            raise JobRefused(400, "unknown job kind %r (known: %s)" % (kind, ", ".join(self.kinds())))
        fn, uses = self._handlers[kind]
        params = dict(params or {})
        uses_llm = uses(params) if callable(uses) else bool(uses)
        with self._lock:
            if uses_llm:
                if self.llm_jobs_started >= self.max_llm_jobs:
                    raise JobRefused(429, "model-run limit reached (%d this session). Raise it with "
                                          "lab.jobs.max_llm_jobs = N in the notebook." % self.max_llm_jobs)
                if self._llm_running:
                    raise JobRefused(429, "a model run is already in progress; wait for it to finish")
                self.llm_jobs_started += 1
                self._llm_running += 1
            job = Job(uuid.uuid4().hex[:10], kind, params, uses_llm)
            self._jobs[job.id] = job
        bus.emit("job %s queued (%s)" % (job.id, kind), src="jobs", kind="job", job=job.id, data={"kind": kind})
        self._pool.submit(self._run, job, fn)
        return job

    def _run(self, job: Job, fn) -> None:
        job.status, job.started = "running", time.time()
        try:
            job.result = fn(job)
            job.status = "done"
        except Exception as exc:
            job.status, job.error = "error", "%s: %s" % (type(exc).__name__, str(exc)[:500])
            bus.error("job %s failed: %s" % (job.id, job.error), src="jobs", job=job.id)
        finally:
            job.ended = time.time()
            if job.uses_llm:
                with self._lock:
                    self._llm_running -= 1
            bus.emit("job %s %s in %.1f s" % (job.id, job.status, job.ended - job.started), src="jobs", kind="job_end",
                     job=job.id, data={"status": job.status, "error": job.error})

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def recent(self, n: int = 20) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created, reverse=True)[:n]

    def wait(self, job_id: str, timeout: float = 600) -> Job:
        t = time.time() + timeout
        while time.time() < t:
            j = self._jobs[job_id]
            if j.status in ("done", "error"):
                return j
            time.sleep(0.2)
        raise TimeoutError("job %s still %s after %.0f s" % (job_id, self._jobs[job_id].status, timeout))
