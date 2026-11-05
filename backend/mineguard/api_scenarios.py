"""Read-only API for the deterministic industrial scenario replays (FINAL-ENGINEERING-15-R1). Pure computation: no run, no file, no model, no provider; under the same local security middleware as every route."""
import re

from fastapi import HTTPException

from . import scenarios

_SID = re.compile(r"[a-z0-9_]{3,48}")


def register_scenarios(app):
    @app.get("/api/scenarios")
    def scenario_list():
        return {"schema": "mg.scenario.list/1", "provenance_kind": "SYNTHETIC_SCENARIO_REPLAY",
                "scenarios": [{"id": s, "case": scenarios.SCENARIOS[s]["case"]} for s in scenarios.scenario_ids()]}

    @app.get("/api/scenarios/{sid}")
    def scenario_get(sid: str):
        if not _SID.fullmatch(sid) or sid not in scenarios.SCENARIOS:
            raise HTTPException(404, {"msg_id": "mg.refusal.scenario_unknown"})
        return scenarios.run_scenario(sid)
