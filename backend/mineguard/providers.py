"""Perception / explanation PROVIDER STATUS (read-only). One place that states, honestly, what this build can and cannot run.

Nothing here contacts a provider, reads a credential, or can enable spending: the states of the external providers are fixed by construction (this build contains no client and no real transport),
and the local model state is derived from the weights verification the application already performs. Credentials found in the environment are NOT inspected and NOT authorization."""
from __future__ import annotations

from .costgov import GovernorConfig, NullRealTransport, SpendGovernor

STATES = ("LOCAL_READY", "LOCAL_UNAVAILABLE", "META_DISABLED", "CREDENTIALS_NOT_CONFIGURED", "BILLING_NOT_APPROVED", "LIVE_NOT_VERIFIED", "SIMULATED", "ERROR")


def _local(model: dict) -> dict:
    verified = model.get("verified")
    states = ["LOCAL_READY"] if verified is True else (["LOCAL_UNAVAILABLE"] if verified is False else ["ERROR"])
    return {"provider_id": "local_rfdetr", "kind": "local_inference", "label_id": "mg.prov.local_rfdetr", "states": states, "real_inference": verified is True, "external_call_possible": False,
            "model_id": model.get("model_id"), "weights_sha256": model.get("weights_sha256"), "weights_verified": verified, "detail": model.get("error"),
            "accuracy_validated": False, "note_id": "mg.prov.note.local"}


def status(model: dict) -> dict:
    """`model` is the result of the application's own weights check (`/api/model`)."""
    gov = SpendGovernor(GovernorConfig())                                 # DEFAULT config (deny everything); the environment is deliberately not consulted
    external = lambda pid, label, note, extra=(): {"provider_id": pid, "kind": "external_inference", "label_id": label, "states": ["META_DISABLED" if pid == "meta_sam31" else "CREDENTIALS_NOT_CONFIGURED",
                                                    *(["CREDENTIALS_NOT_CONFIGURED"] if pid == "meta_sam31" else []), "BILLING_NOT_APPROVED", *extra], "real_inference": False,
                                                    "external_call_possible": False, "note_id": note}
    providers = [
        _local(model),
        external("meta_sam31", "mg.prov.meta_sam31", "mg.prov.note.meta", ("LIVE_NOT_VERIFIED",)),
        {"provider_id": "muse_spark", "kind": "explanation", "label_id": "mg.prov.muse", "states": ["SIMULATED"], "real_inference": False, "external_call_possible": False, "note_id": "mg.prov.note.muse"},
        external("baseten", "mg.prov.baseten", "mg.prov.note.abstract"),
        external("modal", "mg.prov.modal", "mg.prov.note.abstract"),
    ]
    return {"schema": "mg.providers/1", "providers": providers, "states_vocabulary": list(STATES),
            "cost": {**gov.snapshot(), "real_transport": NullRealTransport.__name__, "authorized_external_spend_usd": "0", "authorized_external_calls": 0,
                     "ui_can_enable_spending": False, "zero_charge_guaranteed": False,
                     "note_id": "mg.prov.note.cost"},
            "invariants": {"external_calls_possible": False, "credentials_read": False, "environment_consulted_for_providers": False}}
