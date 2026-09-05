#!/usr/bin/env python3
"""
Loopback-only web dashboard for Vista Slurm jobs.

Usage:
  python vista_job_dashboard.py --user USERNAME
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse


def run_cmd(cmd: list[str], timeout: int = 15) -> str:
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            message = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
            raise RuntimeError(f"Command failed: {' '.join(cmd)} ({message})")
        return proc.stdout.strip()
    except FileNotFoundError:
        raise RuntimeError(f"Command not found: {cmd[0]}")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Command timeout ({timeout}s): {' '.join(cmd)}")
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Command failed: {' '.join(cmd)} ({e})")


def parse_gpu_line(line: str) -> dict[str, str]:
    """Parse a single CSV line from nvidia-smi query-gpu output."""
    fields = [x.strip() for x in line.split(",")]
    if len(fields) < 12:
        return {}
    util_gpu = to_int(fields[1])
    util_mem = to_int(fields[2])
    mem_used = to_int(fields[3])
    mem_total = to_int(fields[4])
    temp = to_int(fields[5])
    power_draw = fields[6]
    sm_clock = to_int(fields[7])
    mem_clock = to_int(fields[8])
    pcie_gen = to_int(fields[9])
    pcie_width = to_int(fields[10])
    mem_pct = round(100 * mem_used / mem_total, 1) if mem_total else 0.0
    return {
        "index": fields[0],
        "name": fields[11] if len(fields) >= 12 else "GPU",
        "gpu_util": util_gpu,
        "mem_util": util_mem,
        "mem_used": mem_used,
        "mem_total": mem_total,
        "mem_used_pct": mem_pct,
        "temp": temp,
        "power": power_draw,
        "sm_clock": sm_clock,
        "mem_clock": mem_clock,
        "pcie_gen": pcie_gen,
        "pcie_width": pcie_width,
        "pcie_link": f"{pcie_gen}x{pcie_width}",
        "raw": line,
    }


def to_int(v: str) -> int:
    v = v.strip()
    if v in {"", "N/A", "[Not Supported]"}:
        return 0
    try:
        return int(float(v))
    except ValueError:
        return 0


def parse_slurm_duration_seconds(value: str) -> float:
    """Convert Slurm time values such as D-HH:MM:SS or HH:MM:SS to seconds."""
    text = (value or "").strip()
    if text in {"", "N/A", "Unknown", "None"}:
        return 0.0
    days = 0
    if "-" in text:
        day_text, text = text.split("-", 1)
        try:
            days = int(day_text)
        except ValueError:
            return 0.0
    try:
        parts = [float(part) for part in text.split(":")]
    except ValueError:
        return 0.0
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours, minutes, seconds = 0.0, parts[0], parts[1]
    elif len(parts) == 1:
        hours, minutes, seconds = 0.0, 0.0, parts[0]
    else:
        return 0.0
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_slurm_memory_mb(value: str) -> float:
    """Convert Slurm memory values with K/M/G/T suffixes to MiB."""
    text = (value or "").strip()
    if text in {"", "0", "N/A", "Unknown", "None"}:
        return 0.0
    match = re.fullmatch(r"([0-9.]+)([KMGTP]?)", text, re.IGNORECASE)
    if not match:
        return 0.0
    number = float(match.group(1))
    suffix = match.group(2).upper()
    factors = {"": 1 / (1024 * 1024), "K": 1 / 1024, "M": 1, "G": 1024, "T": 1024**2, "P": 1024**3}
    return number * factors[suffix]


def parse_squeue_output(raw: str) -> list[dict[str, str]]:
    jobs = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < 18:
            continue
        (
            jobid,
            name,
            state,
            used,
            wall_left,
            time_limit,
            reason,
            nodes,
            partition,
            qos,
            account,
            num_nodes,
            num_cpus,
            memory,
            gres,
            dependency,
            submit_time,
            start_time,
        ) = parts[:18]
        jobs.append(
            {
                "jobid": jobid.strip(),
                "name": name.strip(),
                "state": state.strip(),
                "used": used.strip(),
                "wall_left": wall_left.strip(),
                "reason": reason.strip(),
                "nodes": nodes.strip(),
                "partition": partition.strip(),
                "qos": qos.strip(),
                "account": account.strip(),
                "num_nodes": num_nodes.strip(),
                "num_cpus": num_cpus.strip(),
                "memory": memory.strip(),
                "gres": gres.strip(),
                "dependency": dependency.strip(),
                "submit_time": submit_time.strip(),
                "start_time": start_time.strip(),
                "time_limit": time_limit.strip(),
            }
        )
    return jobs


def parse_kv_output(raw: str) -> dict[str, str]:
    """Parse Slurm's one-line key/value output while preserving spaces in values."""
    fields: dict[str, str] = {}
    matches = list(re.finditer(r"(?:^|\s)([A-Za-z][A-Za-z0-9_/:]*)=", raw))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        fields[match.group(1)] = raw[start:end].strip()
    return fields


def query_job_detail(jobid: str) -> dict[str, str]:
    if not shutil.which("scontrol"):
        return {}
    try:
        raw = run_cmd(["scontrol", "show", "job", jobid, "-o"], timeout=15)
    except RuntimeError:
        return {}
    return parse_kv_output(raw)


def query_estimated_starts(user: str) -> dict[str, dict[str, str]]:
    """Fetch scheduler start estimates for all of the user's pending jobs at once."""
    try:
        raw = run_cmd(
            ["squeue", "--start", "-h", "-u", user, "-o", "%i|%S|%R"],
            timeout=20,
        )
    except RuntimeError:
        return {}
    estimates: dict[str, dict[str, str]] = {}
    for line in raw.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            estimates[parts[0].strip()] = {
                "start": parts[1].strip(),
                "reason": parts[2].strip(),
            }
    return estimates


def query_priority_components(user: str) -> dict[str, dict[str, str]]:
    """Fetch sprio's priority breakdown for pending jobs."""
    if not shutil.which("sprio"):
        return {}
    fmt = "%i|%Y|%A|%B|%F|%J|%P|%Q|%S|%N|%T"
    try:
        raw = run_cmd(["sprio", "-u", user, "-h", "-o", fmt], timeout=20)
    except RuntimeError:
        return {}
    priorities: dict[str, dict[str, str]] = {}
    labels = [
        "jobid",
        "total",
        "age",
        "association",
        "fairshare",
        "job_size",
        "partition",
        "qos",
        "site",
        "nice",
        "tres",
    ]
    for line in raw.splitlines():
        parts = [part.strip() for part in line.split("|")]
        if len(parts) >= len(labels):
            row = dict(zip(labels, parts[: len(labels)]))
            priorities[row["jobid"]] = row
    return priorities


def list_jobs(
    user: str,
    partitions: list[str] | None,
    job_name: str | None,
    states: str,
    job_id: str | None = None,
) -> list[dict[str, str]]:
    if not shutil.which("squeue"):
        raise RuntimeError("squeue not found. Please run on a Slurm login node.")

    fmt = "%i|%j|%T|%M|%L|%l|%R|%N|%P|%q|%a|%D|%C|%m|%b|%E|%V|%S"
    cmd = ["squeue", "-h", "-u", user, "-o", fmt]
    if states:
        cmd += ["-t", states]
    if job_id:
        cmd += ["-j", job_id]
    if partitions:
        cmd += ["-p", ",".join(partitions)]
    raw = run_cmd(cmd, timeout=20)
    jobs = parse_squeue_output(raw)

    if job_name:
        lower = job_name.lower()
        jobs = [j for j in jobs if lower in j["name"].lower()]
    return jobs


HISTORY_FIELDS = [
    "JobIDRaw",
    "JobName",
    "State",
    "Elapsed",
    "Timelimit",
    "Partition",
    "QOS",
    "Account",
    "AllocNodes",
    "AllocCPUS",
    "ReqMem",
    "ReqTRES",
    "AllocTRES",
    "NodeList",
    "ExitCode",
    "Submit",
    "Eligible",
    "Start",
    "End",
    "WorkDir",
]
HISTORY_CACHE_SECONDS = 60
_history_cache: dict[tuple[object, ...], tuple[float, list[dict[str, object]]]] = {}
_history_cache_lock = threading.Lock()


def accounting_row_to_job(row: dict[str, str]) -> dict[str, object]:
    """Normalize one sacct job row to the same shape as an active job."""
    state = row["State"].split()[0].split("+")[0]
    status_badge = {
        "COMPLETED": "✅",
        "FAILED": "🔴",
        "CANCELLED": "🟠",
        "TIMEOUT": "🟣",
        "OUT_OF_MEMORY": "🧠",
        "NODE_FAIL": "⚫",
        "PREEMPTED": "🟤",
    }.get(state, "⚪")
    return {
        "jobid": row["JobIDRaw"],
        "name": row["JobName"],
        "state": row["State"],
        "status_icon": status_badge,
        "used": row["Elapsed"],
        "wall_left": "N/A",
        "time_limit": row["Timelimit"],
        "partition": row["Partition"],
        "qos": row["QOS"],
        "account": row["Account"],
        "num_nodes": row["AllocNodes"],
        "num_cpus": row["AllocCPUS"],
        "num_tasks": "N/A",
        "cpus_per_task": "N/A",
        "memory": row["ReqMem"],
        "gres": "N/A",
        "req_tres": row["ReqTRES"],
        "alloc_tres": row["AllocTRES"],
        "nodes": row["NodeList"],
        "batch_host": "N/A",
        "features": "N/A",
        "exit_code": row["ExitCode"],
        "submit_time": row["Submit"],
        "eligible_time": row["Eligible"],
        "accrue_time": "N/A",
        "start_time": row["Start"],
        "estimated_start": row["Start"],
        "end_time": row["End"],
        "deadline": "N/A",
        "last_sched_eval": "N/A",
        "scheduler": "N/A",
        "priority": "N/A",
        "priority_components": {},
        "reason": state,
        "reason_detail": state,
        "dependency": "N/A",
        "array_job_id": "N/A",
        "array_task_id": "N/A",
        "requeue": "N/A",
        "restarts": "N/A",
        "nice": "N/A",
        "command": "N/A",
        "work_dir": row["WorkDir"],
        "stdout": "N/A",
        "stderr": "N/A",
        "stdin": "N/A",
        "gpu": {"metrics": [], "processes": []},
        "gpu_warn": "Historical GPU telemetry was not retained",
        "cpu": {"total_cpu_seconds": 0, "rss_mb": 0, "max_rss_mb": 0, "vmem_mb": 0, "max_vmem_mb": 0, "steps": []},
        "cpu_warn": "Historical CPU telemetry was not retained",
        "slurm_details": row,
        "is_history": True,
    }


def query_accounting_job(user: str, job_id: str) -> dict[str, object] | None:
    """Return a completed/failed job by exact ID from Slurm accounting."""
    if not shutil.which("sacct"):
        return None
    cmd = [
        "sacct",
        "-j",
        job_id,
        "-u",
        user,
        "-X",
        "-n",
        "-P",
        f"--format={','.join(HISTORY_FIELDS)}",
    ]
    try:
        raw = run_cmd(cmd, timeout=20)
    except RuntimeError:
        return None
    for line in raw.splitlines():
        values = line.split("|")
        if values and values[-1] == "":
            values.pop()
        if len(values) < len(HISTORY_FIELDS):
            continue
        row = dict(zip(HISTORY_FIELDS, values[: len(HISTORY_FIELDS)]))
        if row["JobIDRaw"] == job_id:
            return accounting_row_to_job(row)
    return None


def list_history(
    user: str,
    partitions: list[str] | None,
    job_name: str | None,
    hours: int,
    limit: int,
) -> list[dict[str, object]]:
    """Return recent terminal jobs from Slurm accounting, excluding job steps."""
    if hours <= 0 or not shutil.which("sacct"):
        return []
    cache_key = (user, tuple(partitions or []), job_name or "", hours, limit)
    with _history_cache_lock:
        cached = _history_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < HISTORY_CACHE_SECONDS:
            return cached[1]
    terminal_states = {
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        "TIMEOUT",
        "OUT_OF_MEMORY",
        "NODE_FAIL",
        "PREEMPTED",
    }
    cmd = [
        "sacct",
        "-S",
        f"now-{hours}hours",
        "-u",
        user,
        "-X",
        "-n",
        "-P",
        f"--format={','.join(HISTORY_FIELDS)}",
    ]
    try:
        raw = run_cmd(cmd, timeout=30)
    except RuntimeError:
        return []

    partition_filter = set(partitions or [])
    name_filter = (job_name or "").lower()
    history: list[dict[str, object]] = []
    for line in raw.splitlines():
        values = line.split("|")
        if values and values[-1] == "":
            values.pop()
        if len(values) < len(HISTORY_FIELDS):
            continue
        row = dict(zip(HISTORY_FIELDS, values[: len(HISTORY_FIELDS)]))
        state = row["State"].split()[0].split("+")[0]
        if state not in terminal_states:
            continue
        if partition_filter and row["Partition"] not in partition_filter:
            continue
        if name_filter and name_filter not in row["JobName"].lower():
            continue
        history.append(accounting_row_to_job(row))
    history.sort(key=lambda item: str(item.get("end_time", "")), reverse=True)
    result = history[:limit]
    with _history_cache_lock:
        if len(_history_cache) > 16:
            _history_cache.clear()
        _history_cache[cache_key] = (time.monotonic(), result)
    return result


def list_recent_terminal_queue_jobs(
    user: str,
    partitions: list[str] | None,
    job_name: str | None,
) -> list[dict[str, object]]:
    """Capture terminal jobs still visible to squeue, including never-started cancellations.

    Vista's accounting time-range query can omit a job cancelled before it ever
    became eligible or started.  Such jobs remain queryable by exact ID for a
    short time, so discover those IDs through squeue and then fetch their full
    accounting rows.
    """
    try:
        terminal_rows = list_jobs(
            user=user,
            partitions=partitions,
            job_name=job_name,
            states="CA,F,TO,NF,OOM,PR,CD",
        )
    except RuntimeError:
        return []

    history: list[dict[str, object]] = []
    for row in terminal_rows:
        item = query_accounting_job(user, row["jobid"])
        if item is not None:
            history.append(item)
    return history


def merge_history_jobs(
    accounting_history: list[dict[str, object]],
    terminal_queue_history: list[dict[str, object]],
    limit: int,
) -> list[dict[str, object]]:
    """Merge history sources by Job ID and keep the newest terminal jobs first."""
    merged = {
        str(item.get("jobid", "")): item
        for item in accounting_history
        if item.get("jobid")
    }
    for item in terminal_queue_history:
        if item.get("jobid"):
            merged[str(item["jobid"])] = item
    return sorted(
        merged.values(),
        key=lambda item: str(item.get("end_time", "")),
        reverse=True,
    )[:limit]


def query_job_gpu(jobid: str) -> tuple[dict, str | None]:
    """
    Query GPU status for a running job via an inline one-off srun step.
    """
    if not shutil.which("srun"):
        return {"error": "srun not available"}, None

    # Query GPU summary
    srun_cmd = [
        "srun",
        "--overlap",
        f"--jobid={jobid}",
        "--ntasks=1",
        "--nodes=1",
        "--time=00:00:10",
        "nvidia-smi",
        "--query-gpu=index,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw,clocks.current.sm,clocks.current.memory,pcie.link.gen.current,pcie.link.width.current,name",
        "--format=csv,noheader,nounits",
    ]
    try:
        gpu_raw = run_cmd(srun_cmd, timeout=20)
    except RuntimeError as e:
        return {"error": str(e), "metrics": []}, None

    if not gpu_raw:
        return {"metrics": [], "processes": []}, "No GPU output"

    metrics = []
    for line in gpu_raw.splitlines():
        parsed = parse_gpu_line(line)
        if parsed:
            metrics.append(parsed)

    # Query processes on device (single call)
    proc_cmd = [
        "srun",
        "--overlap",
        f"--jobid={jobid}",
        "--ntasks=1",
        "--nodes=1",
        "--time=00:00:10",
        "nvidia-smi",
        "--query-compute-apps=pid,process_name,used_memory",
        "--format=csv,noheader,nounits",
    ]
    try:
        proc_raw = run_cmd(proc_cmd, timeout=20)
    except RuntimeError as e:
        return {"error": str(e), "metrics": metrics, "processes": []}, None

    procs = []
    for line in (proc_raw or "").splitlines():
        cols = [x.strip() for x in line.split(",")]
        if len(cols) >= 3 and cols[0].isdigit():
            procs.append({"pid": cols[0], "name": cols[1], "mem": cols[2]})

    return {"metrics": metrics, "processes": procs}, None


def query_job_cpu(jobid: str) -> tuple[dict[str, object], str | None]:
    """Read cumulative CPU time and live memory statistics for all running steps."""
    if not shutil.which("sstat"):
        return {"total_cpu_seconds": 0, "rss_mb": 0, "max_rss_mb": 0, "vmem_mb": 0, "max_vmem_mb": 0, "steps": []}, "sstat not available"
    fields = [
        "JobID",
        "AveCPU",
        "AveRSS",
        "MaxRSS",
        "AveVMSize",
        "MaxVMSize",
        "NTasks",
        "TRESUsageInTot",
    ]
    try:
        raw = run_cmd(
            ["sstat", "-a", "-j", jobid, "-n", "-P", f"--format={','.join(fields)}"],
            timeout=20,
        )
    except RuntimeError as error:
        return {"total_cpu_seconds": 0, "rss_mb": 0, "max_rss_mb": 0, "vmem_mb": 0, "max_vmem_mb": 0, "steps": [], "error": str(error)}, None

    total_cpu_seconds = 0.0
    rss_mb = 0.0
    max_rss_mb = 0.0
    vmem_mb = 0.0
    max_vmem_mb = 0.0
    steps: list[dict[str, object]] = []
    for line in raw.splitlines():
        values = line.split("|", len(fields) - 1)
        if len(values) < len(fields):
            continue
        row = dict(zip(fields, values))
        ntasks = max(1, to_int(row["NTasks"]))
        tres = {}
        for item in row["TRESUsageInTot"].split(","):
            if "=" in item:
                key, value = item.split("=", 1)
                tres[key] = value
        cpu_seconds = parse_slurm_duration_seconds(tres.get("cpu", ""))
        if not cpu_seconds:
            cpu_seconds = parse_slurm_duration_seconds(row["AveCPU"]) * ntasks
        step_rss_mb = parse_slurm_memory_mb(row["AveRSS"]) * ntasks
        step_vmem_mb = parse_slurm_memory_mb(row["AveVMSize"]) * ntasks
        total_cpu_seconds += cpu_seconds
        rss_mb += step_rss_mb
        vmem_mb += step_vmem_mb
        max_rss_mb = max(max_rss_mb, parse_slurm_memory_mb(row["MaxRSS"]))
        max_vmem_mb = max(max_vmem_mb, parse_slurm_memory_mb(row["MaxVMSize"]))
        steps.append(
            {
                "job_step": row["JobID"],
                "tasks": ntasks,
                "cpu_seconds": round(cpu_seconds, 3),
                "rss_mb": round(step_rss_mb, 2),
                "max_rss_mb": round(parse_slurm_memory_mb(row["MaxRSS"]), 2),
                "vmem_mb": round(step_vmem_mb, 2),
            }
        )
    warning = None if steps else "No running Slurm steps returned CPU statistics"
    return {
        "total_cpu_seconds": round(total_cpu_seconds, 3),
        "rss_mb": round(rss_mb, 2),
        "max_rss_mb": round(max_rss_mb, 2),
        "vmem_mb": round(vmem_mb, 2),
        "max_vmem_mb": round(max_vmem_mb, 2),
        "steps": steps,
    }, warning


def collect_all(
    user: str,
    partitions: list[str] | None,
    job_name: str | None,
    states: str,
    job_id: str | None = None,
    include_gpu: bool = False,
) -> list[dict[str, object]]:
    jobs = list_jobs(
        user=user,
        partitions=partitions,
        job_name=job_name,
        states=states,
        job_id=job_id,
    )
    if job_id is None:
        active_states = {
            "PENDING",
            "PD",
            "RUNNING",
            "R",
            "CONFIGURING",
            "CF",
            "COMPLETING",
            "CG",
            "SUSPENDED",
            "S",
            "STOPPED",
            "ST",
        }
        jobs = [job for job in jobs if job.get("state") in active_states]
    estimated_starts = query_estimated_starts(user)
    priority_components = query_priority_components(user)
    now = int(time.time())
    out = []
    for j in jobs:
        detail = query_job_detail(j["jobid"])
        tres_text = " ".join(
            str(value)
            for value in (
                detail.get("ReqTRES", ""),
                detail.get("AllocTRES", ""),
                j.get("gres", ""),
            )
        )
        has_gpu_allocation = bool(
            re.search(r"(?:gres/)?gpu(?::[^=,\s]+)?=\d+", tres_text, re.IGNORECASE)
        )
        is_running = j["state"] in {"RUNNING", "R", "RUNNING+"}
        if include_gpu and is_running and has_gpu_allocation:
            data, warn = query_job_gpu(j["jobid"])
            entry = {
                "gpu": data,
                "gpu_warn": warn,
            }
        elif include_gpu and is_running:
            entry = {
                "gpu": {"metrics": [], "processes": []},
                "gpu_warn": "No GPU resource is allocated to this job",
            }
        else:
            entry = {"gpu": {"metrics": [], "processes": []}, "gpu_warn": None}

        if include_gpu and is_running:
            cpu_data, cpu_warn = query_job_cpu(j["jobid"])
            entry["cpu"] = cpu_data
            entry["cpu_warn"] = cpu_warn
        else:
            entry["cpu"] = {
                "total_cpu_seconds": 0,
                "rss_mb": 0,
                "max_rss_mb": 0,
                "vmem_mb": 0,
                "max_vmem_mb": 0,
                "steps": [],
            }
            entry["cpu_warn"] = None

        estimate = estimated_starts.get(j["jobid"], {})
        priority = priority_components.get(j["jobid"], {})
        status_badge = {
            "RUNNING": "🟢",
            "PD": "🟡",
            "PENDING": "🟡",
            "COMPLETED": "✅",
            "CD": "✅",
            "F": "🔴",
            "FAILED": "🔴",
            "CG": "🔵",
            "TO": "🟣",
            "CA": "🟠",
        }.get(j["state"], "⚪")

        out.append(
            {
                **j,
                **entry,
                "status_icon": status_badge,
                "updated_at": now,
                "priority": detail.get("Priority", priority.get("total", "N/A")),
                "priority_components": priority,
                "submit_time": detail.get("SubmitTime", j.get("submit_time", "N/A")),
                "start_time": detail.get("StartTime", j.get("start_time", "N/A")),
                "estimated_start": estimate.get("start", detail.get("StartTime", "N/A")),
                "eligible_time": detail.get("EligibleTime", "N/A"),
                "accrue_time": detail.get("AccrueTime", "N/A"),
                "end_time": detail.get("EndTime", "N/A"),
                "deadline": detail.get("Deadline", "N/A"),
                "reason_detail": detail.get("Reason", ""),
                "last_sched_eval": detail.get("LastSchedEval", "N/A"),
                "scheduler": detail.get("Scheduler", "N/A"),
                "nice": detail.get("Nice", priority.get("nice", "N/A")),
                "num_nodes": detail.get("NumNodes", j.get("num_nodes", "N/A")),
                "num_cpus": detail.get("NumCPUs", j.get("num_cpus", "N/A")),
                "num_tasks": detail.get("NumTasks", "N/A"),
                "cpus_per_task": detail.get("CPUs/Task", "N/A"),
                "req_tres": detail.get("ReqTRES", "N/A"),
                "alloc_tres": detail.get("AllocTRES", "N/A"),
                "memory": j.get("memory", "N/A"),
                "gres": j.get("gres", "N/A"),
                "batch_host": detail.get("BatchHost", "N/A"),
                "features": detail.get("Features", "N/A"),
                "dependency": detail.get("Dependency", j.get("dependency", "N/A")),
                "array_job_id": detail.get("ArrayJobId", "N/A"),
                "array_task_id": detail.get("ArrayTaskId", "N/A"),
                "requeue": detail.get("Requeue", "N/A"),
                "restarts": detail.get("Restarts", "N/A"),
                "exit_code": detail.get("ExitCode", "N/A"),
                "command": detail.get("Command", "N/A"),
                "work_dir": detail.get("WorkDir", "N/A"),
                "stdout": detail.get("StdOut", "N/A"),
                "stderr": detail.get("StdErr", "N/A"),
                "stdin": detail.get("StdIn", "N/A"),
                "slurm_details": detail,
            }
        )
    return out


def dashboard_html() -> str:
    return r"""<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Vista Job CPU/GPU Dashboard</title>
<style>
  :root { --bg: #0f1117; --panel: #171a21; --text: #e6edf3; --muted: #9aa4b2; --ok:#2ea043; --warn:#d29922; --bad:#f85149; --line:#2d333b; }
  body { margin: 0; font-family: system-ui; background: var(--bg); color: var(--text); }
  .wrap { max-width: 1500px; margin: 18px auto; padding: 0 16px; }
  h1 { margin: 6px 0 4px; }
  .meta { color: var(--muted); margin-bottom: 12px; }
  .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 12px; margin-bottom: 12px; }
  .grid { display: grid; grid-template-columns: 1fr; gap: 10px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { color: #f0f6fc; font-weight: 600; }
  .row { background: #1f242d; }
  .small { color: var(--muted); font-size: 12px; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; margin: 0 4px 0 0; }
  .pill-ok { background:#0e4429; color:#3fb950; }
  .pill-warn { background:#4d2d00; color:#d29922; }
  .pill-bad { background:#4e1117; color:#f85149; }
  .meter { width: 130px; height: 10px; border-radius: 999px; background: #2e3540; position: relative; overflow: hidden; }
  .bar { position: absolute; left: 0; top: 0; bottom: 0; background: #58a6ff; }
  .bar.ok { background:#2ea043; }
  .bar.warn { background:#d29922; }
  .bar.bad { background:#f85149; }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
  code { background:#11161d; padding:1px 5px; border-radius:4px; }
  .proc { margin-top: 6px; color: #9fd0ff; }
  .stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:8px; margin-top:12px; }
  .stat { background:#11161d; border:1px solid var(--line); border-radius:7px; padding:10px; }
  .stat-value { font-size:22px; font-weight:700; }
  .details-row td { padding:0 8px 12px; background:#1f242d; }
  .detail-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:8px; }
  .detail-box { background:#151920; border:1px solid var(--line); border-radius:7px; padding:10px; min-width:0; }
  .detail-box h3 { margin:0 0 7px; font-size:13px; color:#f0f6fc; }
  .kv { display:grid; grid-template-columns:minmax(105px,auto) 1fr; gap:3px 8px; font-size:12px; }
  .kv .key { color:var(--muted); }
  .value { overflow-wrap:anywhere; }
  details { margin-top:8px; }
  summary { cursor:pointer; color:#9fd0ff; }
  .raw-grid { max-height:360px; overflow:auto; margin-top:8px; }
  .history-wrap { overflow:auto; }
  .active-wrap { overflow:auto; }
  .active-wrap > table { min-width:1050px; }
  .history-row-failed { background:#2a171b; }
  .topbar { display:flex; align-items:center; justify-content:space-between; gap:12px; }
  .button { color:var(--text); background:#21262d; border:1px solid #4b5563; border-radius:6px; padding:7px 11px; cursor:pointer; text-decoration:none; }
  .button:hover { border-color:#58a6ff; }
  .job-link { color:#79c0ff; text-decoration:none; }
  .job-link:hover { text-decoration:underline; }
  .chart-link { display:inline-block; margin-top:7px; padding:5px 8px; font-size:12px; }
  .charts { display:grid; grid-template-columns:repeat(auto-fit,minmax(420px,1fr)); gap:10px; }
  .chart { background:#11161d; border:1px solid var(--line); border-radius:8px; padding:10px; min-width:0; }
  .chart h3 { margin:0 0 3px; font-size:14px; }
  .chart svg { display:block; width:100%; height:190px; }
  .legend { display:flex; flex-wrap:wrap; gap:8px; margin-top:4px; }
  .legend-item { font-size:11px; color:var(--muted); }
  .legend-dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:4px; }
  .command-flow { white-space:pre-wrap; line-height:1.65; color:#c9d1d9; }
  .command-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(360px,1fr)); gap:10px; }
  .command-card { background:#11161d; border:1px solid var(--line); border-radius:8px; padding:12px; min-width:0; }
  .command-card h3 { margin:0 0 5px; font-size:15px; }
  .command-head { display:flex; justify-content:space-between; align-items:flex-start; gap:10px; }
  .command-code { display:block; margin-top:10px; padding:10px; background:#090c10; border:1px solid #30363d; border-radius:6px; color:#aff5b4; overflow:auto; white-space:pre; }
  .where { color:#79c0ff; font-size:11px; margin-top:4px; }
  .danger-note { color:#ffb3ad; }
  .command-section h2 { margin:2px 0 5px; }
  .command-section + .command-section { margin-top:20px; }
  .hidden { display:none !important; }
  @media (max-width: 800px) { .wrap { padding:0 8px; } th,td { padding:6px; } .meter { width:80px; } }
</style>
</head>
<body>
<div class="wrap">
  <div class="topbar" style="margin-bottom:10px;">
    <a id="home-link" class="button hidden" href="/">← <span data-i18n="all_jobs">全部作业</span></a>
    <div style="flex:1"></div>
    <a id="commands-link" class="button" href="/commands">⌨ <span data-i18n="command_ref">常用命令</span></a>
    <button id="lang-toggle" class="button" type="button">English</button>
  </div>
  <div id="home-view">
  <div class="panel">
    <h1 data-i18n="dashboard_title">Vista 作业、CPU 与 GPU 仪表盘</h1>
    <div class="meta">
      <span data-i18n="auto_refresh">自动刷新</span>: <span id="interval"></span> <span data-i18n="seconds">秒</span> ·
      <span data-i18n="last_update">最后更新</span>: <span id="ts"></span> ·
      <span data-i18n="total_jobs">任务总数</span>: <span id="count">0</span>
    </div>
    <div class="small" data-i18n="gpu_page_hint">📈 点击作业旁的“查看 CPU/GPU 图表”进入真实折线图页面。</div>
<div class="small"><span data-i18n="filters">当前筛选</span>: <span data-i18n="user_label">用户</span> <span id="user"></span> · <span data-i18n="partition_label">分区</span> <span id="parts"></span> · <span data-i18n="name_contains">作业名包含</span> <span id="jobfilter"></span></div>
    <div id="summary" class="stats"></div>
  </div>
  <div id="container" class="panel">
    <div class="small" data-i18n="loading">加载中…</div>
  </div>
  <div id="history" class="panel">
    <div class="small" data-i18n="loading">加载中…</div>
  </div>
  </div>
  <div id="job-view" class="hidden">
    <div class="panel">
      <h1 id="job-title">Job</h1>
      <div class="meta"><span data-i18n="auto_refresh">自动刷新</span>: <span id="job-interval"></span> s · <span data-i18n="last_update">最后更新</span>: <span id="job-ts">—</span></div>
      <div id="job-headline" class="stats"></div>
    </div>
    <div id="job-overview" class="panel"><div class="small" data-i18n="loading">加载中…</div></div>
    <div class="panel">
      <h2 data-i18n="cpu_charts">CPU 与内存实时曲线</h2>
      <div class="small" data-i18n="cpu_chart_note">CPU 利用率根据 Slurm 累计 CPU 时间在相邻样本间计算，并按已分配 CPU 数归一化。</div>
      <div id="cpu-charts" class="charts"></div>
    </div>
    <div class="panel">
      <h2 data-i18n="gpu_charts">GPU 实时曲线</h2>
      <div class="small" data-i18n="chart_note">曲线从打开详情页后开始采样；近期样本保存在本机浏览器中。</div>
      <div id="gpu-charts" class="charts"></div>
    </div>
  </div>
  <div id="commands-view" class="hidden">
    <div class="panel">
      <h1 data-i18n="commands_title">Vista 常用命令</h1>
      <div class="meta" data-i18n="commands_intro">按运行位置整理；点击复制后粘贴到对应终端。复制按钮不会自动执行命令。</div>
      <div class="command-flow" data-i18n="commands_flow">Mac 本地终端 → Vista 登录节点 → Slurm 调度 → 计算节点 / IDE</div>
    </div>
    <div class="panel">
      <div class="small" data-i18n="placeholder_note">使用前请把 JOB_ID、your-job.sbatch 和 WINDOW_NAME 等占位符替换成实际值。sbatch、scancel、scontrol update 会改变作业状态，请确认后再执行。</div>
    </div>
    <div id="commands-container"></div>
  </div>
</div>
<script>
const params = new URLSearchParams(window.location.search);
const interval = parseInt(params.get('interval') || '5', 10);
const commandPage = window.location.pathname === '/commands' || window.location.pathname === '/commands/';
const jobMatch = window.location.pathname.match(/^\/job\/(.+)$/);
const detailJobId = jobMatch ? decodeURIComponent(jobMatch[1]) : '';
const user = params.get('user') || '';
const partitions = params.get('partitions') || '';
const job = params.get('job') || '';
const states = params.get('states') || 'PD,R,CF,CG,S,ST';
const historyHours = Math.max(0, Math.min(168, parseInt(params.get('history_hours') || '24', 10)));
const historyLimit = Math.max(1, Math.min(500, parseInt(params.get('history_limit') || '100', 10)));
const I18N = {
  zh: {
    all_jobs:'全部作业', dashboard_title:'Vista 作业、CPU 与 GPU 仪表盘', auto_refresh:'自动刷新',
    last_update:'最后更新', gpu_charts:'GPU 实时曲线', cpu_charts:'CPU 与内存实时曲线',
    chart_note:'曲线从打开详情页后开始采样；近期样本保存在本机浏览器中。',
    cpu_chart_note:'CPU 利用率根据 Slurm 累计 CPU 时间在相邻样本间计算，并按已分配 CPU 数归一化。',
    active_jobs:'活动作业', running:'运行中', pending:'排队中', nodes_requested:'节点请求',
    cpus_requested:'CPU 请求', gpus_allocated:'GPU 分配', finished:'已结束', abnormal:'异常结束',
    job:'任务', elapsed_left:'耗时 / 剩时', scheduling:'调度信息', placement:'分区 / QoS / 节点',
    gpu_live:'GPU 实况', recent_finished:'最近 {hours} 小时已结束作业（最多 {limit} 条）',
    no_active:'当前没有匹配到活动任务。', no_history:'最近 {hours} 小时没有匹配到已结束作业。',
    estimated_start:'预计启动', unavailable:'暂不可预测', submit:'提交', start:'启动', limit:'时限',
    remaining:'剩余', reason:'理由', priority:'优先级', sched_times:'调度时间', priority_parts:'优先级构成',
    resources:'请求与分配资源', job_relations:'放置与作业关系', paths:'执行位置与输出',
    full_fields:'完整 Slurm 字段（{count} 项）', no_detail:'此作业没有可用的 scontrol 详情。',
    no_gpu:'尚无 GPU 指标；曲线将按 0 记录。', gpu_processes:'GPU 进程', samples:'样本',
    current_value:'当前值', no_job:'找不到这个作业，或者 Slurm accounting 尚未记录它。',
    load_failed:'刷新失败', state:'状态', name:'名称', partition_qos:'分区 / QoS',
    elapsed_limit:'耗时 / 时限', resource:'资源', start_end:'开始 / 结束', exit_detail:'退出码 / 详情', details:'详情',
    seconds:'秒', total_jobs:'任务总数', filters:'当前筛选', user_label:'用户', partition_label:'分区',
    name_contains:'作业名包含', current_user:'当前登录用户', all:'全部', loading:'加载中…',
    trend:'近期趋势（本地采样）', insufficient:'采样不足', non_running:'当前非运行状态，GPU 指标按 0 记录。',
    no_gpu_row:'未返回 GPU 设备数据。', quick_details:'展开快速详情', command_ref:'常用命令',
    commands_title:'Vista 常用命令', commands_intro:'按运行位置整理；点击复制后粘贴到对应终端。复制按钮不会自动执行命令。',
    commands_flow:'Mac 本地终端 → Vista 登录节点 → Slurm 调度 → 计算节点 / IDE',
    placeholder_note:'使用前请把 JOB_ID、your-job.sbatch 和 WINDOW_NAME 等占位符替换成实际值。sbatch、scancel、scontrol update 会改变作业状态，请确认后再执行。',
    copy:'复制', copied:'已复制', view_charts:'查看 CPU/GPU 图表',
    gpu_page_hint:'📈 点击作业旁的“查看 CPU/GPU 图表”进入真实折线图页面。'
  },
  en: {
    all_jobs:'All jobs', dashboard_title:'Vista Jobs, CPU & GPU Dashboard', auto_refresh:'Auto refresh',
    last_update:'Last update', gpu_charts:'Live GPU Charts', cpu_charts:'Live CPU & Memory Charts',
    chart_note:'Sampling begins when this page opens; recent samples persist in this browser.',
    cpu_chart_note:'CPU utilization is derived from the change in Slurm cumulative CPU time between samples, normalized by allocated CPUs.',
    active_jobs:'Active jobs', running:'Running', pending:'Pending', nodes_requested:'Nodes requested',
    cpus_requested:'CPUs requested', gpus_allocated:'GPUs allocated', finished:'finished', abnormal:'Abnormal exits',
    job:'Job', elapsed_left:'Elapsed / Left', scheduling:'Scheduling', placement:'Partition / QoS / Nodes',
    gpu_live:'Live GPU', recent_finished:'Finished jobs in last {hours}h (up to {limit})',
    no_active:'No matching active jobs.', no_history:'No matching finished jobs in the last {hours} hours.',
    estimated_start:'Estimated start', unavailable:'Not currently predictable', submit:'Submitted', start:'Started', limit:'Limit',
    remaining:'Remaining', reason:'Reason', priority:'Priority', sched_times:'Scheduling times', priority_parts:'Priority factors',
    resources:'Requested & allocated resources', job_relations:'Placement & relationships', paths:'Execution & output paths',
    full_fields:'All Slurm fields ({count})', no_detail:'No scontrol details are available for this job.',
    no_gpu:'No GPU metrics yet; charts will record zero.', gpu_processes:'GPU processes', samples:'samples',
    current_value:'Current', no_job:'This job was not found, or Slurm accounting has not recorded it yet.',
    load_failed:'Refresh failed', state:'State', name:'Name', partition_qos:'Partition / QoS',
    elapsed_limit:'Elapsed / Limit', resource:'Resources', start_end:'Start / End', exit_detail:'Exit / Details', details:'Details',
    seconds:'seconds', total_jobs:'Total jobs', filters:'Filters', user_label:'User', partition_label:'Partition',
    name_contains:'Job name contains', current_user:'current login user', all:'all', loading:'Loading…',
    trend:'Recent trend (local samples)', insufficient:'Not enough samples', non_running:'Job is not running; GPU metrics are recorded as zero.',
    no_gpu_row:'No GPU device data was returned.', quick_details:'Expand quick details', command_ref:'Commands',
    commands_title:'Common Vista Commands', commands_intro:'Organized by where each command runs. Copy, then paste into the matching terminal; copying never executes it.',
    commands_flow:'Local Mac terminal → Vista login node → Slurm scheduler → Compute node / IDE',
    placeholder_note:'Replace placeholders such as JOB_ID, your-job.sbatch, and WINDOW_NAME before use. sbatch, scancel, and scontrol update change job state; verify before running them.',
    copy:'Copy', copied:'Copied', view_charts:'View CPU/GPU charts',
    gpu_page_hint:'📈 Use “View CPU/GPU charts” beside a job to open its real line-chart page.'
  }
};
function readLanguagePreference() {
  const cookie = document.cookie.split('; ').find(item => item.startsWith('vistaDashboardLanguage='));
  const cookieValue = cookie ? decodeURIComponent(cookie.split('=').slice(1).join('=')) : '';
  const localValue = localStorage.getItem('vista-dashboard-language') || '';
  return ['zh', 'en'].includes(cookieValue) ? cookieValue : (['zh', 'en'].includes(localValue) ? localValue : 'zh');
}
function saveLanguagePreference(value) {
  localStorage.setItem('vista-dashboard-language', value);
  document.cookie = `vistaDashboardLanguage=${encodeURIComponent(value)}; Max-Age=31536000; Path=/; SameSite=Lax`;
}
let language = readLanguagePreference();
function t(key, values = {}) {
  let text = (I18N[language] && I18N[language][key]) || I18N.zh[key] || key;
  for (const [name, value] of Object.entries(values)) text = text.replace(`{${name}}`, value);
  return text;
}
function applyLanguage() {
  document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en';
  document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
  document.getElementById('lang-toggle').textContent = language === 'zh' ? 'English' : '中文';
  document.getElementById('user').textContent = user || t('current_user');
  document.getElementById('parts').textContent = partitions || t('all');
  document.getElementById('jobfilter').textContent = job || t('all');
}
document.getElementById('lang-toggle').addEventListener('click', () => {
  language = language === 'zh' ? 'en' : 'zh';
  saveLanguagePreference(language);
  applyLanguage();
  if (detailJobId && latestDetailJob) renderJobDetail(latestDetailJob);
  if (!detailJobId && !commandPage && latestHomeData) renderHome(latestHomeData);
  if (commandPage) renderCommands();
});
applyLanguage();
document.getElementById('interval').textContent = interval;
document.getElementById('ts').textContent = '等待首帧';
document.getElementById('job-interval').textContent = interval;
if (commandPage) {
  document.getElementById('home-view').classList.add('hidden');
  document.getElementById('commands-view').classList.remove('hidden');
  document.getElementById('home-link').classList.remove('hidden');
  document.getElementById('commands-link').classList.add('hidden');
} else if (detailJobId) {
  document.getElementById('home-view').classList.add('hidden');
  document.getElementById('job-view').classList.remove('hidden');
  document.getElementById('home-link').classList.remove('hidden');
}
let latestHomeData = null;
let latestDetailJob = null;

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
  })[ch]);
}

function shown(value, fallback = 'N/A') {
  const text = String(value ?? '').trim();
  return esc(text && text !== '(null)' ? text : fallback);
}

const openDetailKeys = new Set();
function restoreDetailState(root) {
  root.querySelectorAll('details[data-persist-key]').forEach(detail => {
    const key = detail.dataset.persistKey;
    detail.open = openDetailKeys.has(key);
    detail.addEventListener('toggle', () => {
      if (detail.open) openDetailKeys.add(key);
      else openDetailKeys.delete(key);
    });
  });
}

const COMMAND_SECTIONS = [
  {
    title:{zh:'1. 本地 Mac：登录、申请节点和打开工具', en:'1. Local Mac: login, allocate, and open tools'},
    intro:{zh:'这些命令在你自己的 Mac 终端运行，不是在 Vista 里面运行。', en:'Run these in your Mac terminal, not inside Vista.'},
    commands:[
      {name:{zh:'建立可复用 SSH 主连接',en:'Start the reusable SSH master'}, code:'ssh -MNf __LOGIN_ALIAS__', desc:{zh:'需要新认证时输入一次 TACC token；之后的 SSH、IDE 和仪表盘尽量复用它。',en:'Enter a TACC token when fresh authentication is required; later SSH, IDE, and dashboard connections reuse it when possible.'}},
      {name:{zh:'检查 SSH 主连接',en:'Check the SSH master'}, code:'ssh -O check __LOGIN_ALIAS__', desc:{zh:'显示 Master running 代表主连接仍可复用。',en:'“Master running” means the shared connection is still available.'}},
      {name:{zh:'进入登录节点',en:'Open a login-node shell'}, code:'ssh __LOGIN_ALIAS__', desc:{zh:'提交、取消和检查 Slurm 作业应在登录节点完成。',en:'Submit, cancel, and inspect Slurm jobs from a login node.'}},
      {name:{zh:'申请 gh 节点并打开 Cursor',en:'Allocate gh and open Cursor'}, code:'vista-allocate gh 6', desc:{zh:'申请或复用一个 6 小时 gh 节点，等待运行后打开仪表盘和 Cursor。命令名是 vista-allocate。',en:'Allocate or reuse a 6-hour gh node, then open the dashboard and Cursor when it starts. The command is spelled vista-allocate.'}},
      {name:{zh:'申请 gg 节点并打开 Cursor',en:'Allocate gg and open Cursor'}, code:'vista-allocate gg 6', desc:{zh:'适用于 gg 分区；最后的数字表示小时。',en:'Use the gg partition; the final number is the requested hours.'}},
      {name:{zh:'申请短时 gh-dev 节点',en:'Allocate a short gh-dev node'}, code:'vista-allocate gh-dev 2', desc:{zh:'gh-dev 最长 2 小时，适合短时开发；排队更快并不保证。',en:'gh-dev has a 2-hour maximum and suits short development sessions; a faster start is not guaranteed.'}},
      {name:{zh:'改用 VS Code',en:'Open VS Code instead'}, code:'vista-allocate gh 6 code', desc:{zh:'流程相同，但节点就绪后打开 VS Code。',en:'Uses the same workflow but opens VS Code when ready.'}},
      {name:{zh:'只申请节点，不打开 IDE',en:'Allocate without an IDE'}, code:'vista-allocate gh 6 none', desc:{zh:'等待并更新动态计算节点地址，但不启动 Cursor 或 VS Code。',en:'Waits and updates the dynamic compute target without launching Cursor or VS Code.'}},
      {name:{zh:'连接当前计算节点',en:'Connect to the current compute node'}, code:'ssh __COMPUTE_ALIAS__', desc:{zh:'只有分配已经运行且动态节点地址已更新时才可用。',en:'Works after the allocation is running and the dynamic node target has been updated.'}},
      {name:{zh:'单独打开作业仪表盘',en:'Open the job dashboard'}, code:'vista-dashboard-open', desc:{zh:'启动登录节点上的监控服务、建立本地转发并打开浏览器。',en:'Starts monitoring on the login node, creates the local tunnel, and opens the browser.'}},
      {name:{zh:'关闭 SSH 主连接',en:'Close the SSH master'}, code:'ssh -O exit __LOGIN_ALIAS__', desc:{zh:'只关闭本机到登录节点的共享 SSH 连接，不会取消 Slurm 作业。',en:'Closes the shared SSH connection to the login node; it does not cancel Slurm jobs.'}, danger:true}
    ]
  },
  {
    title:{zh:'2. 登录节点：队列和分区状态', en:'2. Login node: queues and partitions'},
    intro:{zh:'先在本地运行 ssh __LOGIN_ALIAS__，再粘贴这些命令。', en:'Run ssh __LOGIN_ALIAS__ locally first, then paste these commands.'},
    commands:[
      {name:{zh:'查看自己的活动作业',en:'List your active jobs'}, code:'squeue -u "$USER"', desc:{zh:'显示排队、运行和正在结束的作业。',en:'Shows your pending, running, and completing jobs.'}},
      {name:{zh:'查看预计启动时间',en:'Show estimated start times'}, code:'squeue --start -u "$USER"', desc:{zh:'Slurm 可能无法预测；给出的时间也会随队列变化。',en:'Slurm may be unable to predict a time, and estimates can move as the queue changes.'}},
      {name:{zh:'查看三个常用分区',en:'Inspect common partitions'}, code:"sinfo -p gg,gh,gh-dev -o '%P|%a|%l|%D|%t|%G'", desc:{zh:'查看分区状态、时间上限、节点状态和 GRES。',en:'Shows partition state, time limit, node states, and GRES.'}},
      {name:{zh:'持续刷新自己的队列',en:'Watch your queue'}, code:"watch -n 5 'squeue -u \"$USER\"'", desc:{zh:'每 5 秒刷新一次；按 Ctrl+C 退出。',en:'Refreshes every five seconds; press Ctrl+C to stop.'}},
      {name:{zh:'查看指定作业详情',en:'Inspect one job'}, code:'scontrol show job JOB_ID', desc:{zh:'显示资源请求、工作目录、输出路径、等待原因等完整字段。',en:'Shows resource requests, working directory, output paths, wait reason, and other fields.'}},
      {name:{zh:'查看指定作业优先级',en:'Inspect job priority'}, code:'sprio -j JOB_ID', desc:{zh:'查看 age、fair-share、partition、QoS 等优先级组成。',en:'Shows age, fair-share, partition, QoS, and other priority factors.'}}
    ]
  },
  {
    title:{zh:'3. 登录节点：提交、修改和取消作业', en:'3. Login node: submit, modify, and cancel jobs'},
    intro:{zh:'Vista 不允许从计算节点提交新作业；请在登录节点执行。', en:'Vista does not allow new submissions from compute nodes; run these on a login node.'},
    commands:[
      {name:{zh:'检查 sbatch 脚本但不提交',en:'Validate an sbatch script without submitting'}, code:'sbatch --test-only your-job.sbatch', desc:{zh:'让 Slurm 检查请求是否可接受，不创建真实作业。',en:'Asks Slurm to validate the request without creating a real job.'}},
      {name:{zh:'提交批处理作业',en:'Submit a batch job'}, code:'sbatch your-job.sbatch', desc:{zh:'提交后记录返回的 Job ID。',en:'Record the returned Job ID after submission.'}, danger:true},
      {name:{zh:'提交并保存 Job ID',en:'Submit and capture the Job ID'}, code:'jid=$(sbatch --parsable your-job.sbatch); echo "$jid"', desc:{zh:'把新 Job ID 保存到当前 shell 的 jid 变量中。',en:'Stores the new Job ID in the current shell variable jid.'}, danger:true},
      {name:{zh:'取消一个作业',en:'Cancel one job'}, code:'scancel JOB_ID', desc:{zh:'立即取消指定作业；先用 squeue 或 scontrol 确认 Job ID。',en:'Cancels the selected job; verify the Job ID with squeue or scontrol first.'}, danger:true},
      {name:{zh:'修改排队作业的时限',en:'Change a pending job time limit'}, code:'scontrol update JobId=JOB_ID TimeLimit=06:00:00', desc:{zh:'只有权限和作业状态允许时才会成功；这会修改真实作业。',en:'Succeeds only when permissions and job state allow it; this modifies a real job.'}, danger:true},
      {name:{zh:'查看单个作业的预计启动',en:'Estimate one job start'}, code:'squeue --start -j JOB_ID', desc:{zh:'比查看整个队列更适合复制某个任务的预测信息。',en:'Convenient for checking one job’s scheduler estimate.'}}
    ]
  },
  {
    title:{zh:'4. 登录节点：历史、输出和资源监控', en:'4. Login node: history, output, and resource monitoring'},
    intro:{zh:'用于诊断运行中或已经结束的任务。', en:'Use these to diagnose running or finished work.'},
    commands:[
      {name:{zh:'查看作业历史和退出状态',en:'Show job history and exit state'}, code:'sacct -j JOB_ID --format=JobID,JobName,Partition,State,Elapsed,Start,End,ExitCode,AllocTRES', desc:{zh:'包括 batch 和 extern 步骤，可用于判断任务是否正常结束。',en:'Includes batch and extern steps and helps determine whether the job ended normally.'}},
      {name:{zh:'查看运行中步骤的 CPU/内存',en:'Show live CPU and memory statistics'}, code:'sstat -j JOB_ID.batch --format=JobID,AveCPU,AveRSS,MaxRSS', desc:{zh:'通常只对正在运行且已有 batch step 的作业有数据。',en:'Usually returns data only for a running job with a batch step.'}},
      {name:{zh:'持续查看标准输出',en:'Follow standard output'}, code:'tail -f slurm-JOB_ID.out', desc:{zh:'按 Ctrl+C 停止跟踪；文件名可被 sbatch 脚本中的 --output 改写。',en:'Press Ctrl+C to stop; --output in the sbatch script may change the filename.'}},
      {name:{zh:'查看错误输出',en:'Read standard error'}, code:'less slurm-JOB_ID.err', desc:{zh:'如果 stdout/stderr 合并或自定义，请先用 scontrol show job 查路径。',en:'If output is combined or customized, use scontrol show job to find the path.'}},
      {name:{zh:'查询运行中 GPU 作业',en:'Query a running GPU job'}, code:'srun --overlap --jobid=JOB_ID --ntasks=1 --nodes=1 nvidia-smi', desc:{zh:'从登录节点在已有运行作业内启动短步骤；CPU-only 或未运行的作业不会有 GPU 数据。',en:'Launches a short step inside an existing running job from the login node; CPU-only or non-running jobs have no GPU data.'}}
    ]
  },
  {
    title:{zh:'5. 计算节点：目录和 Codex 恢复', en:'5. Compute node: directory and Codex recovery'},
    intro:{zh:'先通过 vista-allocate 或 ssh __COMPUTE_ALIAS__ 进入计算节点。', en:'Enter the compute node with vista-allocate or ssh __COMPUTE_ALIAS__ first.'},
    commands:[
      {name:{zh:'进入共享 scratch 目录',en:'Enter shared scratch'}, code:'cd "$SCRATCH"', desc:{zh:'共享目录会跨计算节点保留。',en:'The shared directory persists across compute nodes.'}},
      {name:{zh:'恢复所有活动 Codex 窗口',en:'Restore active Codex windows'}, code:'~/start.sh', desc:{zh:'重建仍标记为活动的 screen/tmux 窗口，并连接其中一个。',en:'Rebuilds still-active screen/tmux windows and attaches one.'}},
      {name:{zh:'列出 Codex 窗口状态',en:'List Codex window state'}, code:'~/start.sh --list', desc:{zh:'查看哪些窗口会在下一节点恢复。',en:'Shows which windows will be restored on the next node.'}},
      {name:{zh:'新建 Codex 窗口',en:'Create a Codex window'}, code:'~/start.sh --new WINDOW_NAME', desc:{zh:'创建独立窗口；把 WINDOW_NAME 换成容易识别的名称。',en:'Creates an independent window; replace WINDOW_NAME with a memorable name.'}},
      {name:{zh:'连接指定 Codex 窗口',en:'Attach a Codex window'}, code:'~/start.sh WINDOW_NAME', desc:{zh:'连接已有窗口；首次使用时可选择对应 Codex session。',en:'Attaches an existing window; first use can select the matching Codex session.'}},
      {name:{zh:'关闭并停止以后恢复',en:'Close and exclude from future restore'}, code:'~/start.sh --close WINDOW_NAME', desc:{zh:'关闭该托管窗口，并从下一节点的自动恢复列表移除。',en:'Closes the managed window and removes it from future automatic recovery.'}, danger:true},
      {name:{zh:'查看 screen 会话',en:'List screen sessions'}, code:'screen -ls', desc:{zh:'当前 Vista 计算镜像通常使用 GNU screen 作为 tmux 的替代。',en:'The current Vista compute image commonly uses GNU screen as the tmux fallback.'}}
    ]
  }
];

function renderCommands() {
  document.title = language === 'zh' ? 'Vista 常用命令' : 'Common Vista Commands';
  document.getElementById('commands-container').innerHTML = COMMAND_SECTIONS.map(section => `
    <section class="panel command-section">
      <h2>${esc(section.title[language])}</h2>
      <div class="meta">${esc(section.intro[language])}</div>
      <div class="command-grid">${section.commands.map(command => `
        <article class="command-card">
          <div class="command-head"><div><h3>${esc(command.name[language])}</h3><div class="where">${esc(section.title[language].split(':')[0].split('：')[0])}</div></div>
          <button class="button copy-command" type="button" data-command="${esc(command.code)}">${esc(t('copy'))}</button></div>
          <div class="small ${command.danger ? 'danger-note' : ''}">${esc(command.desc[language])}</div>
          <code class="command-code">${esc(command.code)}</code>
        </article>`).join('')}</div>
    </section>`).join('');
}

async function copyCommand(button) {
  const command = button.dataset.command || '';
  try {
    await navigator.clipboard.writeText(command);
  } catch (_) {
    const area = document.createElement('textarea');
    area.value = command;
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    document.execCommand('copy');
    area.remove();
  }
  button.textContent = t('copied');
  setTimeout(() => { button.textContent = t('copy'); }, 1200);
}

document.getElementById('commands-view').addEventListener('click', event => {
  const button = event.target.closest('.copy-command');
  if (button) copyCommand(button);
});

function kvRows(rows) {
  return `<div class="kv">${rows.map(([key, value]) =>
    `<div class="key">${esc(key)}</div><div class="value mono">${shown(value)}</div>`
  ).join('')}</div>`;
}

function barStyle(v) {
  const i = Number(v || 0);
  const cls = i > 85 ? 'bad' : i > 60 ? 'warn' : 'ok';
  return `width:${Math.max(0, Math.min(100, i))}%;`;
}

const HISTORY_LIMIT = 60;
const gpuHistories = {};

function safeNumber(v, fallback = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

function recordHistories(jobs) {
  const now = Date.now();
  for (const j of jobs) {
    const metrics = (j.gpu && j.gpu.metrics) || [];
    const running = (j.state === 'RUNNING' || j.state === 'R' || j.state === 'RUNNING+');
    if (!running || !metrics.length) {
      continue;
    }
    const gpuCount = metrics.length || 1;
    const avgGpu = metrics.length
      ? metrics.reduce((acc, it) => acc + safeNumber(it.gpu_util), 0) / gpuCount
      : 0;
    const avgMem = metrics.length
      ? metrics.reduce((acc, it) => acc + safeNumber(it.mem_used_pct), 0) / gpuCount
      : 0;
    const avgTemp = metrics.length
      ? metrics.reduce((acc, it) => acc + safeNumber(it.temp), 0) / gpuCount
      : 0;
    const power = metrics.length
      ? metrics.reduce((acc, it) => acc + safeNumber(parseFloat(String(it.power).replace(' W', ''))), 0) / gpuCount
      : 0;

    if (!gpuHistories[j.jobid]) {
      gpuHistories[j.jobid] = [];
    }
    gpuHistories[j.jobid].push({
      t: now,
      gpu: avgGpu,
      mem: avgMem,
      temp: avgTemp,
      power: power,
    });
    if (gpuHistories[j.jobid].length > HISTORY_LIMIT) {
      gpuHistories[j.jobid].shift();
    }
  }
}

function sanitizeNumber(v, fallback = 0) {
  return Number.isFinite(v) ? v : fallback;
}

function sparkline(points, valueKey, color) {
  const width = 260;
  const height = 40;
  if (!points.length) {
    return `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none"><text x="8" y="22" fill="#9aa4b2" font-size="11">no data</text></svg>`;
  }
  const values = points.map(p => sanitizeNumber(p[valueKey], 0));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(1, max - min);

  const n = points.length;
  const step = n > 1 ? width / (n - 1) : width / 2;
  const path = points
    .map((p, idx) => {
      const x = idx * step;
      const v = sanitizeNumber(p[valueKey], 0);
      const y = height - ((v - min) / span) * (height - 8) - 4;
      return `${idx === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${Math.max(0, Math.min(height, y)).toFixed(2)}`;
    })
    .join(' ');

  return `<div class="small mono">[${valueKey}] ${min.toFixed(1)} ~ ${max.toFixed(1)} · ${points.length} samples</div>
    <svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
      <polyline points="${path}" fill="none" stroke="${color}" stroke-width="1.6" />
    </svg>`;
}

function statusClass(state){
  if (state.startsWith('RUN') || state==='R') return 'pill-ok';
  if (state.includes('PD') || state==='CG') return 'pill-warn';
  if (state.startsWith('F') || state==='FAILED') return 'pill-bad';
  return '';
}

function makeBadge(state) {
  const s = `${state || ''}`;
  return `<span class="pill ${statusClass(s)}">${esc(s)}</span>`;
}

function fullSlurmDetails(job, context = 'job') {
  const details = job.slurm_details || {};
  const entries = Object.entries(details).sort(([a], [b]) => a.localeCompare(b));
  if (!entries.length) return `<div class="small">${esc(t('no_detail'))}</div>`;
  return `<details data-persist-key="${esc(`${context}:${job.jobid}:slurm`)}"><summary>${esc(t('full_fields', {count: entries.length}))}</summary>
    <div class="raw-grid">${kvRows(entries)}</div></details>`;
}

function rowFor(job) {
  const gpulist = (job.gpu && job.gpu.metrics) || [];
  const hist = gpuHistories[job.jobid] || [];
  let gpuHtml = `<div class="small">${esc(t('no_gpu'))}</div>`;
  if (job.state.startsWith('R') || job.state==='RUNNING' || job.state==='RUNNING+' ) {
    if (job.gpu && job.gpu.error) {
      gpuHtml = `<div class="small">⚠ ${shown(job.gpu.error)}</div>`;
    } else if (gpulist.length > 0) {
      gpuHtml = '';
      for (const g of gpulist) {
        const memPct = Number(g.mem_used_pct || 0);
        gpuHtml += `
          <div style="margin-bottom:8px;">
            <div class="small">${shown(g.name || 'GPU')} ${shown(g.index)} / util ${shown(g.gpu_util)}% · mem ${shown(g.mem_used)}/${shown(g.mem_total)} MB (${memPct}%) · temp ${shown(g.temp)}°C</div>
            <div class="small mono">${shown(g.power)} · SM ${shown(g.sm_clock)}MHz / Mem ${shown(g.mem_clock)}MHz · PCIe ${shown(g.pcie_link)}</div>
            <div style="display:flex; gap:10px; align-items:center; margin-top:4px;">
              <div>GPU</div><div class="meter"><div class="bar ${Number(g.gpu_util) > 85 ? 'bad' : (Number(g.gpu_util) > 60 ? 'warn' : 'ok')}" style="${barStyle(g.gpu_util)}"></div></div> ${g.gpu_util}%
              <div>VRAM</div><div class="meter"><div class="bar ${memPct > 85 ? 'bad' : (memPct > 60 ? 'warn' : 'ok')}" style="${barStyle(memPct)}"></div></div> ${memPct}%
            </div>
          </div>`;
      }
    } else {
      gpuHtml = `<div class="small">${esc(t('no_gpu_row'))}</div>`;
    }
    const procs = (job.gpu && job.gpu.processes) || [];
    if (procs.length > 0) {
      const p = procs.slice(0, 10).map(x => `<div class=mono>${shown(x.pid)}  ${shown(x.name)}  ${shown(x.mem)} MB</div>`).join('');
      gpuHtml += `<div class="proc"><strong>${esc(t('gpu_processes'))}:</strong>${p}</div>`;
    }
    if (hist.length > 0) {
      gpuHtml += `
        <div class="small" style="margin-top:6px;"><strong>${esc(t('trend'))}:</strong></div>
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:2px;">
          <div>
            <div class="small">GPU Util</div>
            ${sparkline(hist, 'gpu', '#2ea043')}
          </div>
          <div>
            <div class="small">Mem Util %</div>
            ${sparkline(hist, 'mem', '#58a6ff')}
          </div>
        </div>`;
    } else {
      gpuHtml += `<div class="small">${esc(t('trend'))}: ${esc(t('insufficient'))}</div>`;
    }
  } else {
    const warn = job.gpu_warn ? `<div class="small">⚠ ${shown(job.gpu_warn)}</div>` : '';
    gpuHtml = `${warn}<div class="small">${esc(t('non_running'))}</div>`;
  }

  const schedInfo = (job.eligible_time && job.eligible_time !== 'N/A') ? job.eligible_time : (job.submit_time || 'N/A');
  const reasonText = job.reason_detail || job.reason || 'N/A';
  const priority = job.priority_components || {};
  const estimatedStart = job.estimated_start && !['N/A', 'Unknown'].includes(job.estimated_start)
    ? job.estimated_start : t('unavailable');
  const arrayInfo = job.array_job_id && job.array_job_id !== 'N/A'
    ? `${job.array_job_id}_${job.array_task_id || '?'}` : 'N/A';

  return `<tr class="row">
    <td>
      <div>${shown(job.status_icon, '')} ${makeBadge(job.state)} <a class="job-link mono" href="/job/${encodeURIComponent(job.jobid)}">${shown(job.jobid)}</a></div>
      <a class="button chart-link" href="/job/${encodeURIComponent(job.jobid)}">📈 ${esc(t('view_charts'))}</a>
      <div class="small mono">name=${shown(job.name)}</div>
      <div class="small mono">${esc(t('priority'))}: ${shown(job.priority)}</div>
      <div class="small">${esc(t('submit'))}: ${shown(job.submit_time)}<br>${esc(t('start'))}: ${shown(job.start_time)}</div>
    </td>
    <td>${shown(job.used, '-')}<br><span class="small">${esc(t('limit'))}: ${shown(job.time_limit)}<br>${esc(t('remaining'))}: ${shown(job.wall_left)}</span></td>
    <td>${esc(t('estimated_start'))}: ${shown(estimatedStart)}<br><span class="small">${esc(t('reason'))}: ${shown(reasonText)}</span></td>
    <td>${shown(job.partition, '-')} / ${shown(job.qos, '-')}<br><span class="small">account: ${shown(job.account)}<br>nodes: ${shown(job.nodes, '-')}</span></td>
    <td>${gpuHtml}</td>
  </tr>
  <tr class="details-row"><td colspan="5">
    <details data-persist-key="${esc(`home:${job.jobid}:quick`)}"><summary>${esc(t('quick_details'))}</summary>
    <div class="detail-grid">
      <section class="detail-box"><h3>⏱ ${esc(t('sched_times'))}</h3>${kvRows([
        ['提交 Submit', job.submit_time], ['可计入排队 Eligible', schedInfo],
        ['累计优先级 Accrue', job.accrue_time], ['预计启动 Estimate', job.estimated_start],
        ['实际启动 Start', job.start_time], ['预计/实际结束 End', job.end_time],
        ['最近调度评估', job.last_sched_eval], ['调度器', job.scheduler], ['Deadline', job.deadline]
      ])}</section>
      <section class="detail-box"><h3>📊 ${esc(t('priority_parts'))}</h3>${kvRows([
        ['总优先级', job.priority], ['Age', priority.age], ['Association', priority.association],
        ['FairShare', priority.fairshare], ['Job size', priority.job_size],
        ['Partition', priority.partition], ['QoS', priority.qos], ['TRES', priority.tres], ['Nice', job.nice]
      ])}</section>
      <section class="detail-box"><h3>🧮 ${esc(t('resources'))}</h3>${kvRows([
        ['节点数', job.num_nodes], ['CPU 数', job.num_cpus], ['任务数', job.num_tasks],
        ['CPU/Task', job.cpus_per_task], ['内存请求', job.memory], ['GRES', job.gres],
        ['ReqTRES', job.req_tres], ['AllocTRES', job.alloc_tres]
      ])}</section>
      <section class="detail-box"><h3>🖥 ${esc(t('job_relations'))}</h3>${kvRows([
        ['NodeList', job.nodes], ['BatchHost', job.batch_host], ['Features', job.features],
        ['Dependency', job.dependency], ['Array', arrayInfo], ['Requeue', job.requeue],
        ['Restarts', job.restarts], ['ExitCode', job.exit_code]
      ])}</section>
      <section class="detail-box"><h3>📁 ${esc(t('paths'))}</h3>${kvRows([
        ['WorkDir', job.work_dir], ['Command', job.command], ['StdOut', job.stdout],
        ['StdErr', job.stderr], ['StdIn', job.stdin]
      ])}</section>
    </div>
    ${fullSlurmDetails(job, 'home')}
    </details>
  </td></tr>`;
}

function historyTable(history) {
  if (!history.length) return `<div class="small">${esc(t('no_history', {hours: historyHours}))}</div>`;
  const rows = history.map(job => {
    const failed = !String(job.state || '').startsWith('COMPLETED');
    return `<tr class="${failed ? 'history-row-failed' : ''}">
      <td>${shown(job.status_icon, '')} ${makeBadge(job.state)}<br><a class="job-link mono" href="/job/${encodeURIComponent(job.jobid)}">${shown(job.jobid)}</a><br><a class="button chart-link" href="/job/${encodeURIComponent(job.jobid)}">📈 ${esc(t('view_charts'))}</a></td>
      <td>${shown(job.name)}</td><td>${shown(job.partition)} / ${shown(job.qos)}</td>
      <td>${shown(job.used)} / ${shown(job.time_limit)}</td>
      <td>${shown(job.num_nodes)} node · ${shown(job.num_cpus)} CPU<br><span class="small mono">${shown(job.alloc_tres)}</span></td>
      <td>${shown(job.start_time)}<br><span class="small">结束: ${shown(job.end_time)}</span></td>
      <td>${shown(job.exit_code)}<details data-persist-key="${esc(`history:${job.jobid}:slurm`)}"><summary>${esc(t('details'))}</summary>${kvRows(Object.entries(job.slurm_details || {}))}</details></td>
    </tr>`;
  }).join('');
  return `<div class="history-wrap"><table><thead><tr><th>${esc(t('state'))} / Job ID</th><th>${esc(t('name'))}</th><th>${esc(t('partition_qos'))}</th><th>${esc(t('elapsed_limit'))}</th><th>${esc(t('resource'))}</th><th>${esc(t('start_end'))}</th><th>${esc(t('exit_detail'))}</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function tresCount(text, kind) {
  const pattern = kind === 'gpu' ? /(?:gres\/gpu|gpu)(?::[^=,]+)?=(\d+)/g : /(?:^|,)cpu=(\d+)/g;
  let total = 0;
  for (const match of String(text || '').matchAll(pattern)) total += Number(match[1] || 0);
  return total;
}

function renderSummary(jobs, history) {
  const running = jobs.filter(j => String(j.state).startsWith('RUN') || j.state === 'R').length;
  const pending = jobs.filter(j => String(j.state).startsWith('PEND') || j.state === 'PD').length;
  const cpus = jobs.reduce((sum, j) => sum + safeNumber(j.num_cpus), 0);
  const nodes = jobs.reduce((sum, j) => sum + safeNumber(j.num_nodes), 0);
  const gpus = jobs.reduce((sum, j) => sum + tresCount(j.alloc_tres !== 'N/A' ? j.alloc_tres : j.req_tres, 'gpu'), 0);
  const failed = history.filter(j => !String(j.state).startsWith('COMPLETED')).length;
  const stats = [
    [t('active_jobs'), jobs.length], [t('running'), running], [t('pending'), pending],
    [t('nodes_requested'), nodes], [t('cpus_requested'), cpus], [t('gpus_allocated'), gpus],
    [`${historyHours}h ${t('finished')}`, history.length], [t('abnormal'), failed]
  ];
  document.getElementById('summary').innerHTML = stats.map(([label, value]) =>
    `<div class="stat"><div class="stat-value">${shown(value, '0')}</div><div class="small">${esc(label)}</div></div>`
  ).join('');
}

function renderHome(data) {
  recordHistories(data.jobs || []);
  document.getElementById('count').textContent = data.jobs.length;
  renderSummary(data.jobs || [], data.history || []);
  const container = document.getElementById('container');
  if (!data.jobs.length) {
    container.innerHTML = `<div class="small">${esc(t('no_active'))}</div>`;
  } else {
    const rows = data.jobs.map(rowFor).join('');
    container.innerHTML = `<h2>${esc(t('active_jobs'))}</h2><div class="active-wrap"><table><thead><tr><th>${esc(t('job'))}</th><th>${esc(t('elapsed_left'))}</th><th>${esc(t('scheduling'))}</th><th>${esc(t('placement'))}</th><th>${esc(t('gpu_live'))}</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }
  const historyContainer = document.getElementById('history');
  historyContainer.innerHTML = `<h2>${esc(t('recent_finished', {hours: historyHours, limit: historyLimit}))}</h2>${historyTable(data.history || [])}`;
  restoreDetailState(container);
  restoreDetailState(historyContainer);
  document.getElementById('ts').textContent = new Date(data.updated_at * 1000).toLocaleTimeString();
}

async function refresh() {
  const api = `/api/status?user=${encodeURIComponent(user)}&partitions=${encodeURIComponent(partitions)}&states=${encodeURIComponent(states)}&job=${encodeURIComponent(job)}&history_hours=${historyHours}&history_limit=${historyLimit}`;
  let data;
  try {
    const res = await fetch(api, {cache: 'no-store'});
    data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  } catch (error) {
    document.getElementById('container').innerHTML = `<div class="pill pill-bad">${esc(t('load_failed'))}</div> <span class="small">${shown(error.message || error)}</span>`;
    return;
  }
  latestHomeData = data;
  renderHome(data);
}

const DETAIL_HISTORY_LIMIT = 720;
const detailHistoryKey = detailJobId ? `vista-gpu-history-${detailJobId}` : '';
const detailCpuHistoryKey = detailJobId ? `vista-cpu-history-${detailJobId}` : '';
let detailSeries = {};
let detailCpuSeries = [];
if (detailHistoryKey) {
  try { detailSeries = JSON.parse(localStorage.getItem(detailHistoryKey) || '{}'); }
  catch (_) { detailSeries = {}; }
  try { detailCpuSeries = JSON.parse(localStorage.getItem(detailCpuHistoryKey) || '[]'); }
  catch (_) { detailCpuSeries = []; }
}

function metricNumber(value) {
  const number = Number.parseFloat(String(value ?? '').replace(/[^0-9.+-]/g, ''));
  return Number.isFinite(number) ? number : 0;
}

function recordDetailSample(job) {
  const metrics = (job.gpu && job.gpu.metrics) || [];
  const byIndex = Object.fromEntries(metrics.map(metric => [String(metric.index), metric]));
  const requestedGpuCount = tresCount(job.alloc_tres !== 'N/A' ? job.alloc_tres : job.req_tres, 'gpu');
  const knownIndexes = new Set([...Object.keys(detailSeries), ...Object.keys(byIndex)]);
  if (!knownIndexes.size) {
    for (let index = 0; index < Math.max(1, requestedGpuCount); index += 1) knownIndexes.add(String(index));
  }
  const now = Date.now();
  for (const index of knownIndexes) {
    const metric = byIndex[index] || {};
    if (!detailSeries[index]) detailSeries[index] = [];
    detailSeries[index].push({
      t: now,
      gpu_util: metricNumber(metric.gpu_util),
      mem_util: metricNumber(metric.mem_util),
      mem_used: metricNumber(metric.mem_used),
      mem_total: metricNumber(metric.mem_total),
      temp: metricNumber(metric.temp),
      power: metricNumber(metric.power),
      sm_clock: metricNumber(metric.sm_clock),
      mem_clock: metricNumber(metric.mem_clock)
    });
    if (detailSeries[index].length > DETAIL_HISTORY_LIMIT) detailSeries[index].splice(0, detailSeries[index].length - DETAIL_HISTORY_LIMIT);
  }
  try { localStorage.setItem(detailHistoryKey, JSON.stringify(detailSeries)); } catch (_) { /* storage is optional */ }

  const cpu = job.cpu || {};
  const previous = detailCpuSeries.length ? detailCpuSeries[detailCpuSeries.length - 1] : null;
  const totalCpuSeconds = metricNumber(cpu.total_cpu_seconds);
  const allocatedCpus = Math.max(1, metricNumber(job.num_cpus));
  let cpuUtil = 0;
  if (previous && totalCpuSeconds >= metricNumber(previous.total_cpu_seconds)) {
    const wallSeconds = Math.max(0.001, (now - metricNumber(previous.t)) / 1000);
    cpuUtil = 100 * (totalCpuSeconds - metricNumber(previous.total_cpu_seconds)) / (wallSeconds * allocatedCpus);
  }
  detailCpuSeries.push({
    t: now,
    cpu_util: Math.max(0, Math.min(100, cpuUtil)),
    rss_mb: metricNumber(cpu.rss_mb),
    max_rss_mb: metricNumber(cpu.max_rss_mb),
    vmem_mb: metricNumber(cpu.vmem_mb),
    max_vmem_mb: metricNumber(cpu.max_vmem_mb),
    total_cpu_seconds: totalCpuSeconds,
    allocated_cpus: allocatedCpus
  });
  if (detailCpuSeries.length > DETAIL_HISTORY_LIMIT) detailCpuSeries.splice(0, detailCpuSeries.length - DETAIL_HISTORY_LIMIT);
  try { localStorage.setItem(detailCpuHistoryKey, JSON.stringify(detailCpuSeries)); } catch (_) { /* storage is optional */ }
}

const chartColors = ['#58a6ff','#3fb950','#d29922','#f778ba','#a371f7','#39c5cf','#ff7b72','#ffa657'];
function lineChart(field, unit, fixedMax = null, suggestedMax = 1, sourceSeries = detailSeries, labelPrefix = 'GPU') {
  const width = 760, height = 190, left = 48, right = 12, top = 12, bottom = 28;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const seriesEntries = Object.entries(sourceSeries).sort(([a], [b]) => String(a).localeCompare(String(b), undefined, {numeric:true}));
  const values = seriesEntries.flatMap(([, points]) => points.map(point => metricNumber(point[field])));
  const observedMax = values.length ? Math.max(...values) : 0;
  const yMax = fixedMax || Math.max(suggestedMax, Math.ceil(observedMax * 1.12));
  const sampleCount = Math.max(1, ...seriesEntries.map(([, points]) => points.length));
  const xFor = index => left + (sampleCount <= 1 ? 0 : index * plotWidth / (sampleCount - 1));
  const yFor = value => top + plotHeight - Math.max(0, Math.min(yMax, value)) * plotHeight / yMax;
  const grid = [0, .25, .5, .75, 1].map(ratio => {
    const y = top + plotHeight * (1 - ratio);
    return `<line x1="${left}" y1="${y}" x2="${width-right}" y2="${y}" stroke="#2d333b" stroke-width="1"/><text x="${left-6}" y="${y+4}" text-anchor="end" fill="#9aa4b2" font-size="10">${(yMax*ratio).toFixed(yMax <= 10 ? 1 : 0)}</text>`;
  }).join('');
  const paths = seriesEntries.map(([index, points], seriesIndex) => {
    const color = chartColors[seriesIndex % chartColors.length];
    if (!points.length) return '';
    const coordinates = points.length === 1
      ? `M ${left} ${yFor(metricNumber(points[0][field]))} L ${width-right} ${yFor(metricNumber(points[0][field]))}`
      : points.map((point, pointIndex) => `${pointIndex ? 'L' : 'M'} ${xFor(pointIndex).toFixed(1)} ${yFor(metricNumber(point[field])).toFixed(1)}`).join(' ');
    return `<path d="${coordinates}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke"/>`;
  }).join('');
  const legend = seriesEntries.map(([index, points], seriesIndex) => {
    const current = points.length ? metricNumber(points[points.length - 1][field]) : 0;
    const seriesName = index === 'all' ? labelPrefix : `${labelPrefix} ${shown(index)}`;
    return `<span class="legend-item"><span class="legend-dot" style="background:${chartColors[seriesIndex % chartColors.length]}"></span>${esc(seriesName)} · ${esc(t('current_value'))}: ${current.toFixed(1)} ${esc(unit)}</span>`;
  }).join('');
  return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img">${grid}<line x1="${left}" y1="${top}" x2="${left}" y2="${top+plotHeight}" stroke="#6e7681"/><line x1="${left}" y1="${top+plotHeight}" x2="${width-right}" y2="${top+plotHeight}" stroke="#6e7681"/>${paths}<text x="${width-right}" y="${height-6}" text-anchor="end" fill="#9aa4b2" font-size="10">${sampleCount} ${esc(t('samples'))}</text></svg><div class="legend">${legend}</div>`;
}

function metricTitle(zh, en) { return language === 'zh' ? zh : en; }
function renderCpuCharts() {
  const cpuSeries = {all: detailCpuSeries};
  const charts = [
    [metricTitle('CPU 利用率（占已分配 CPU）','CPU utilization (% of allocation)'), 'cpu_util', '%', 100, 100],
    [metricTitle('当前常驻内存 RSS','Current resident memory (RSS)'), 'rss_mb', 'MB', null, 1024],
    [metricTitle('最大常驻内存 MaxRSS','Maximum resident memory (MaxRSS)'), 'max_rss_mb', 'MB', null, 1024],
    [metricTitle('当前虚拟内存','Current virtual memory'), 'vmem_mb', 'MB', null, 1024],
    [metricTitle('累计 CPU 时间','Cumulative CPU time'), 'total_cpu_seconds', 's', null, 60]
  ];
  document.getElementById('cpu-charts').innerHTML = charts.map(([title, field, unit, fixedMax, suggestedMax]) =>
    `<section class="chart"><h3>${esc(title)}</h3>${lineChart(field, unit, fixedMax, suggestedMax, cpuSeries, 'CPU')}</section>`
  ).join('');
}

function renderGpuCharts() {
  const charts = [
    [metricTitle('GPU 利用率','GPU utilization'), 'gpu_util', '%', 100, 100],
    [metricTitle('显存控制器利用率','Memory-controller utilization'), 'mem_util', '%', 100, 100],
    [metricTitle('显存用量','VRAM used'), 'mem_used', 'MB', null, 1024],
    [metricTitle('GPU 温度','GPU temperature'), 'temp', '°C', 100, 100],
    [metricTitle('GPU 功耗','GPU power'), 'power', 'W', null, 50],
    [metricTitle('SM 时钟','SM clock'), 'sm_clock', 'MHz', null, 500],
    [metricTitle('显存时钟','Memory clock'), 'mem_clock', 'MHz', null, 500]
  ];
  document.getElementById('gpu-charts').innerHTML = charts.map(([title, field, unit, fixedMax, suggestedMax]) =>
    `<section class="chart"><h3>${esc(title)}</h3>${lineChart(field, unit, fixedMax, suggestedMax)}</section>`
  ).join('');
}

function currentCpuBlock(job) {
  const cpu = job.cpu || {};
  const warning = cpu.error || job.cpu_warn;
  const steps = Array.isArray(cpu.steps) ? cpu.steps.length : 0;
  return `${warning ? `<div class="small">⚠ ${shown(warning)}</div>` : ''}<div class="stats">
    <div class="stat"><div class="stat-value" style="font-size:16px">${shown(job.num_cpus, '0')}</div><div class="small">${esc(metricTitle('已分配 CPU','Allocated CPUs'))}</div></div>
    <div class="stat"><div class="stat-value" style="font-size:16px">${shown(cpu.total_cpu_seconds, '0')} s</div><div class="small">${esc(metricTitle('累计 CPU 时间','Cumulative CPU time'))}</div></div>
    <div class="stat"><div class="stat-value" style="font-size:16px">${shown(cpu.rss_mb, '0')} MB</div><div class="small">RSS</div></div>
    <div class="stat"><div class="stat-value" style="font-size:16px">${shown(cpu.max_rss_mb, '0')} MB</div><div class="small">MaxRSS</div></div>
    <div class="stat"><div class="stat-value" style="font-size:16px">${shown(steps, '0')}</div><div class="small">Slurm steps</div></div>
  </div>`;
}

function currentGpuBlock(job) {
  const metrics = (job.gpu && job.gpu.metrics) || [];
  if (!metrics.length) {
    const warning = (job.gpu && job.gpu.error) || job.gpu_warn;
    return `<div class="small">${esc(t('no_gpu'))}${warning ? `<br>⚠ ${shown(warning)}` : ''}</div>`;
  }
  const rows = metrics.map(metric => `<tr><td>GPU ${shown(metric.index)}</td><td>${shown(metric.name)}</td><td>${shown(metric.gpu_util)}%</td><td>${shown(metric.mem_util)}%</td><td>${shown(metric.mem_used)} / ${shown(metric.mem_total)} MB</td><td>${shown(metric.temp)}°C</td><td>${shown(metric.power)} W</td><td>${shown(metric.sm_clock)} / ${shown(metric.mem_clock)} MHz</td></tr>`).join('');
  return `<div class="history-wrap"><table><thead><tr><th>GPU</th><th>Model</th><th>Util</th><th>Mem util</th><th>VRAM</th><th>Temp</th><th>Power</th><th>SM / Mem clock</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function renderJobDetail(job) {
  latestDetailJob = job;
  const reasonText = job.reason_detail || job.reason || 'N/A';
  const estimate = job.estimated_start && !['N/A', 'Unknown'].includes(job.estimated_start) ? job.estimated_start : t('unavailable');
  document.title = `Job ${job.jobid} · Vista`;
  document.getElementById('job-title').innerHTML = `${shown(job.status_icon, '')} Job ${shown(job.jobid)} · ${shown(job.name)}`;
  document.getElementById('job-headline').innerHTML = [
    [t('state'), job.state], [t('estimated_start'), estimate], [t('priority'), job.priority],
    [metricTitle('已运行','Elapsed'), job.used], [t('remaining'), job.wall_left], [metricTitle('节点','Node'), job.nodes]
  ].map(([label, value]) => `<div class="stat"><div class="stat-value" style="font-size:16px">${shown(value)}</div><div class="small">${esc(label)}</div></div>`).join('');
  document.getElementById('job-overview').innerHTML = `
    <div class="detail-grid">
      <section class="detail-box"><h3>⏱ ${esc(t('sched_times'))}</h3>${kvRows([
        [t('submit'), job.submit_time], ['Eligible', job.eligible_time], ['Accrue', job.accrue_time],
        [t('estimated_start'), estimate], [t('start'), job.start_time], ['End', job.end_time],
        [t('reason'), reasonText], ['LastSchedEval', job.last_sched_eval], ['Scheduler', job.scheduler]
      ])}</section>
      <section class="detail-box"><h3>📊 ${esc(t('priority_parts'))}</h3>${kvRows(Object.entries(job.priority_components || {}))}</section>
      <section class="detail-box"><h3>🧮 ${esc(t('resources'))}</h3>${kvRows([
        ['Nodes', job.num_nodes], ['CPUs', job.num_cpus], ['Tasks', job.num_tasks], ['CPUs/Task', job.cpus_per_task],
        ['Memory', job.memory], ['GRES', job.gres], ['ReqTRES', job.req_tres], ['AllocTRES', job.alloc_tres]
      ])}</section>
      <section class="detail-box"><h3>🖥 ${esc(t('job_relations'))}</h3>${kvRows([
        ['Partition', job.partition], ['QoS', job.qos], ['Account', job.account], ['NodeList', job.nodes],
        ['BatchHost', job.batch_host], ['Dependency', job.dependency], ['ExitCode', job.exit_code], ['Restarts', job.restarts]
      ])}</section>
      <section class="detail-box"><h3>📁 ${esc(t('paths'))}</h3>${kvRows([
        ['WorkDir', job.work_dir], ['Command', job.command], ['StdOut', job.stdout], ['StdErr', job.stderr], ['StdIn', job.stdin]
      ])}</section>
    </div>
    <h2 style="margin-top:16px">${esc(metricTitle('CPU 与内存实况','Live CPU & memory'))}</h2>${currentCpuBlock(job)}
    <h2 style="margin-top:16px">${esc(t('gpu_live'))}</h2>${currentGpuBlock(job)}
    ${fullSlurmDetails(job, 'detail')}`;
  restoreDetailState(document.getElementById('job-overview'));
  renderCpuCharts();
  renderGpuCharts();
}

async function refreshJob() {
  try {
    const res = await fetch(`/api/job/${encodeURIComponent(detailJobId)}?user=${encodeURIComponent(user)}`, {cache:'no-store'});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    recordDetailSample(data.job);
    renderJobDetail(data.job);
    document.getElementById('job-ts').textContent = new Date(data.updated_at * 1000).toLocaleTimeString();
  } catch (error) {
    document.getElementById('job-overview').innerHTML = `<div class="pill pill-bad">${esc(t('load_failed'))}</div> <span class="small">${shown(error.message || t('no_job'))}</span>`;
  }
}

if (commandPage) {
  renderCommands();
} else if (detailJobId) {
  refreshJob();
  setInterval(refreshJob, interval * 1000);
} else {
  refresh();
  setInterval(refresh, interval * 1000);
}
</script>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    def send_json(self, payload: dict[str, object], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/job/"):
            parsed = urlparse(self.path)
            job_id = unquote(parsed.path.removeprefix("/api/job/"))
            if not re.fullmatch(r"[0-9]+(?:_[0-9]+|_\[[0-9,%-]+\])?", job_id):
                self.send_json({"error": "Invalid Slurm job ID"}, status=400)
                return
            q = parse_qs(parsed.query)
            user = q.get("user", [None])[0]
            if not user:
                import getpass

                user = getpass.getuser()
            try:
                active = collect_all(
                    user=user,
                    partitions=None,
                    job_name=None,
                    states="",
                    job_id=job_id,
                    include_gpu=True,
                )
                job = next((item for item in active if item["jobid"] == job_id), None)
                if job is None and active:
                    job = active[0]
                if job is None:
                    job = query_accounting_job(user, job_id)
                if job is None:
                    self.send_json({"error": f"Job {job_id} was not found"}, status=404)
                    return
                self.send_json({"updated_at": int(time.time()), "job": job})
            except RuntimeError as error:
                self.send_json({"error": str(error)}, status=500)
            return

        if self.path.startswith("/api/status"):
            parsed = urlparse(self.path)
            q = parse_qs(parsed.query)
            user = q.get("user", [None])[0]
            parts = q.get("partitions", [None])[0]
            job_name = q.get("job", [None])[0]
            if not user:
                import getpass

                user = getpass.getuser()

            partitions = [p for p in (parts or "").split(",") if p]
            states = q.get("states", ["PD,R,CF,CG,S,ST"])[0]
            try:
                history_hours = max(0, min(168, int(q.get("history_hours", ["24"])[0])))
                history_limit = max(1, min(500, int(q.get("history_limit", ["100"])[0])))
            except ValueError:
                history_hours, history_limit = 24, 100
            try:
                jobs = collect_all(
                    user=user,
                    partitions=partitions or None,
                    job_name=job_name or None,
                    states=states,
                    include_gpu=False,
                )
                accounting_history = list_history(
                    user=user,
                    partitions=partitions or None,
                    job_name=job_name or None,
                    hours=history_hours,
                    limit=history_limit,
                )
                terminal_queue_history = list_recent_terminal_queue_jobs(
                    user=user,
                    partitions=partitions or None,
                    job_name=job_name or None,
                )
                history = merge_history_jobs(
                    accounting_history,
                    terminal_queue_history,
                    history_limit,
                )
                payload = {
                    "updated_at": int(time.time()),
                    "jobs": jobs,
                    "history": history,
                    "history_hours": history_hours,
                    "history_limit": history_limit,
                }
                status = 200
            except RuntimeError as error:
                payload = {"updated_at": int(time.time()), "error": str(error), "jobs": [], "history": []}
                status = 500
            self.send_json(payload, status=status)
            return

        if self.path.startswith("/"):
            page = dashboard_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
            return

        self.send_error(404, "Not found")

    def log_message(self, fmt: str, *args):  # noqa: A003
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--bind",
        default="127.0.0.1",
        help="Address to listen on (default: 127.0.0.1 for SSH-tunnel-only access)",
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--user", default=None, help="Slurm user")
    parser.add_argument(
        "--partitions",
        default="",
        help="Comma separated partition list, e.g. gh,gh-dev. Empty means all partitions.",
    )
    parser.add_argument(
        "--states",
        default="PD,R,CF,CG,S,ST",
        help="Comma separated active Slurm states (squeue -t), default: PD,R,CF,CG,S,ST",
    )
    parser.add_argument("--job", "--job-name", dest="job", default=None, help="Filter by substring in slurm job name")
    parser.add_argument("--interval", type=int, default=5)
    args = parser.parse_args()

    parts = [p.strip() for p in (args.partitions or "").split(",") if p.strip()]
    initial_url = (
        f"http://localhost:{args.port}/"
        f"?partitions={','.join(parts)}&states={args.states}&user={args.user or ''}"
        f"&job={args.job or ''}&interval={args.interval}"
    )

    server = ThreadingHTTPServer((args.bind, args.port), DashboardHandler)
    print(f"Dashboard running: {initial_url}")
    print(
        "Listening on "
        f"{args.bind}:{args.port}. Use an SSH local forward to open it from your computer."
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
