"""Run a duel: clone repo, pick K random tasks, run each N trials via mcbench."""
from __future__ import annotations

import logging
import random
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .config import (
    CLONE_ROOT,
    CLONE_TIMEOUT_SECONDS,
    SINGLE_TASK_TIMEOUT_SECONDS,
    TASKS_PER_DUEL,
    TRIALS_PER_TASK,
)

log = logging.getLogger(__name__)

MCBENCH_ROOT = Path(__file__).resolve().parent.parent / "mcbench"
TASKS_DIR = MCBENCH_ROOT / "tasks"


def list_tasks() -> list[Path]:
    return sorted(TASKS_DIR.glob("**/*.yaml"))


def pick_random_tasks(k: int = TASKS_PER_DUEL) -> list[Path]:
    all_tasks = list_tasks()
    if not all_tasks:
        log.warning("No tasks found under %s", TASKS_DIR)
        return []
    return random.sample(all_tasks, min(k, len(all_tasks)))


def _git(args: list[str], cwd: Optional[Path] = None, timeout: int = CLONE_TIMEOUT_SECONDS) -> bool:
    try:
        subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            check=True,
            timeout=timeout,
            capture_output=True,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        log.warning("git %s failed: %s", " ".join(args), e)
        return False


def clone_repo(repo: str, sha: str, dest: Path) -> bool:
    """Clone `owner/repo` at the given sha. Returns True on success."""
    url = f"https://github.com/{repo}.git"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    if not _git(["clone", "--filter=blob:none", "--no-checkout", url, str(dest)]):
        return False
    if not _git(["fetch", "--depth", "1", "origin", sha], cwd=dest):
        return False
    if not _git(["checkout", sha], cwd=dest):
        return False
    return True


def run_single_task(agent_dir: Path, task_file: Path) -> float:
    """Run one task once via the mcbench Python API. Returns score in [0, 1]."""
    try:
        from mcbench.agents import AgentSpec, SubprocessAgent
        from mcbench.config import load_task
        from mcbench.runner import run_task
        from mcbench.grader import grade
    except ImportError as e:
        log.error("mcbench not importable: %s", e)
        return 0.0

    try:
        task = load_task(task_file)
        spec = AgentSpec(name=agent_dir.name, path=str(agent_dir))
        agent = SubprocessAgent(spec)
        trace = run_task(task, agent)
        report = grade(task, trace)
        return float(report.get("score", 0.0))
    except Exception as e:
        log.warning("run_task failed for %s: %s", task_file.name, e)
        return 0.0


def evaluate_participant(repo: str, sha: str, tasks: list[Path]) -> float:
    """Clone the repo, run all tasks N trials each, return aggregate score (sum across tasks).

    Score range: 0..len(tasks). A missing/broken submission returns 0.0.
    """
    if not repo or not sha or not tasks:
        return 0.0

    clone_dir = Path(CLONE_ROOT) / f"{repo.replace('/', '__')}__{sha[:12]}"
    if not clone_repo(repo, sha, clone_dir):
        log.warning("clone failed: %s@%s", repo, sha)
        return 0.0

    try:
        total = 0.0
        for task_file in tasks:
            trial_scores = [
                run_single_task(clone_dir, task_file) for _ in range(TRIALS_PER_TASK)
            ]
            mean = sum(trial_scores) / len(trial_scores) if trial_scores else 0.0
            total += mean
            log.info("  %s: trials=%s mean=%.3f", task_file.name, trial_scores, mean)
        return total
    finally:
        shutil.rmtree(clone_dir, ignore_errors=True)
