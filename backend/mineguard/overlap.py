"""Repeated-source overlap history of ONE feed run (MG-PROV-028). Pure functions, no I/O.

The RUN-LEVEL provenance is conservative and unchanged: once another ACTIVE feed played the same file during a run, the whole run is a repeated-source simulation (never genuine again, MG-PROV-027).
That label alone would imply the entire run was concurrent, so the run also keeps WHEN the overlap is known to have been present, in SOURCE time (microseconds, strictly increasing across loops):

  interval = {"from_after_us": a, "until_before_us": b}
    a = time of the last committed sample when the overlap was noticed (None: noticed before any sample), so samples with t <= a were committed alone;
    b = time of the first committed sample at which the overlap was found to be over (None: still present / present until the run ended), so samples with t >= b were committed alone again.
Boundaries are bounded by sample times, not exact instants: nothing finer is claimed. At most MAX_INTERVALS disjoint overlaps are kept; if a run has more, `truncated` is set (never silently) and every episode that started after the last kept interval is `unknown` (MG-PROV-029). An event or alert is placed by the time its episode STARTED. A record without this evidence (a summary saved
by an older version) is `unknown`, never `alone`."""
PHASES = ("alone", "from_launch", "before_overlap", "during_overlap", "between_overlaps", "after_overlap", "unknown")
ALERT_PHASES = PHASES + ("mixed",)   # an alert that merged episodes of different phases inside its cooldown says so; its own `event` is the first episode
MAX_INTERVALS = 64


def _int(v, none=False):
    return (v is None and none) or (isinstance(v, int) and not isinstance(v, bool) and -1 <= v <= 10**13)


def valid_doc(d) -> bool:
    """Strict form of an overlap document (also used for the saved summary read back from disk)."""
    try:
        if not (isinstance(d, dict) and set(d) == {"launch_repeated", "intervals", "evidence", "truncated"} and isinstance(d["truncated"], bool) and (d["launch_repeated"] is None or isinstance(d["launch_repeated"], bool)) and d["evidence"] == "sample_time_bounds"
                and isinstance(d["intervals"], list) and len(d["intervals"]) <= MAX_INTERVALS):
            return False
        prev = -2
        for i in d["intervals"]:
            if not (isinstance(i, dict) and set(i) == {"from_after_us", "until_before_us"} and _int(i["from_after_us"], True) and _int(i["until_before_us"], True)):
                return False
            a = -1 if i["from_after_us"] is None else i["from_after_us"]
            if a < prev or (i["until_before_us"] is not None and i["until_before_us"] <= a):
                return False
            prev = a if i["until_before_us"] is None else i["until_before_us"]
        return all(i["until_before_us"] is not None for i in d["intervals"][:-1])
    except (KeyError, TypeError):
        return False


def phase_of(start_us, doc) -> str:
    """Where an episode that started at `start_us` lies relative to the known overlap of its run."""
    if not valid_doc(doc) or not isinstance(start_us, int) or isinstance(start_us, bool):
        return "unknown"
    if doc["launch_repeated"]:
        return "from_launch"
    for k, i in enumerate(doc["intervals"]):
        a = -1 if i["from_after_us"] is None else i["from_after_us"]
        if start_us <= a:
            return "before_overlap" if k == 0 else "between_overlaps"
        if i["until_before_us"] is None or start_us < i["until_before_us"]:
            return "during_overlap"
    if doc["truncated"]:
        return "unknown"       # later overlaps were not recorded: nothing after the last kept interval can be called «alone» or «after»
    return "after_overlap" if doc["intervals"] else "alone"


def legacy_phase(repeated_source: bool) -> str:
    """A record written before the overlap history existed: a run that was never repeated was alone; a repeated one has no interval evidence."""
    return "unknown" if repeated_source else "alone"


def duplicate_status(state: str, shared_now: bool, launch_repeated, shared_during_run: bool, truncated: bool) -> str:
    """MG-PROV-035: four DIFFERENT facts about duplicate playback of one file, never merged. `inactive`: the feed is not playing or paused, so it is not monitoring anything now (its history is a separate fact);
    `active_overlap`: another feed of the same file is playing or paused right now; `historical_repeated`: it overlapped earlier in this run (or was launched as a repeat) but not now; `unknown`: the overlap record
    is truncated or the launch relation was never recorded; `none`. A single active playback beside an idle duplicate is `none`, never active overlap."""
    if state not in ("playing", "paused"):
        return "inactive"
    if shared_now:
        return "active_overlap"
    if launch_repeated or shared_during_run:
        return "historical_repeated"
    if truncated or launch_repeated is None:
        return "unknown"
    return "none"
