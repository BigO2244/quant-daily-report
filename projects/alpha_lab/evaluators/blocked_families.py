"""Fail-closed adapters for families whose frozen inputs are not certified."""

from __future__ import annotations

from typing import Any, Dict

from projects.alpha_lab.factory.errors import ContractValidationError


def _blocked(family: str) -> Dict[str, Any]:
    raise ContractValidationError(
        "{} evaluator cannot run until every frozen data contract is "
        "CERTIFIED_READY and a separately tested adapter implements the frozen "
        "metric without changing the hypothesis".format(family)
    )


def evaluate_caerus_decomposition(packet: Dict[str, Any], *, phase: str) -> Dict[str, Any]:
    return _blocked("Caerus decomposition")


def evaluate_cross_asset_trend(packet: Dict[str, Any], *, phase: str) -> Dict[str, Any]:
    return _blocked("cross-asset trend")


def evaluate_executive_tone_surprise(
    packet: Dict[str, Any], *, phase: str
) -> Dict[str, Any]:
    return _blocked("executive tone surprise")


def evaluate_net_payout_share_issuance(
    packet: Dict[str, Any], *, phase: str
) -> Dict[str, Any]:
    return _blocked("net payout and share issuance")


def evaluate_asset_growth_investment(
    packet: Dict[str, Any], *, phase: str
) -> Dict[str, Any]:
    return _blocked("asset growth and investment")
