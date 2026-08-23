"""Fail-closed v2 Phase-2 preparation contracts; never a data authority."""
from __future__ import annotations

import hashlib, json, math, re
from dataclasses import MISSING, dataclass
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Dict, Mapping, Sequence, Tuple

from projects.alpha_lab.factory.canonical import canonical_hash, canonical_json, format_datetime, require_non_empty, require_sha256
from projects.alpha_lab.factory.errors import ContractValidationError

_ID = {"family_id":r"FAM-\d{4}-\d{3}","hypothesis_id":r"HYP-\d{4}-\d{3}","experiment_id":r"EXP-\d{4}-\d{4}","candidate_id":r"CAND-\d{4}-\d{3}","asset_definition_id":r"DAD-\d{4}-\d{3}","asset_version_id":r"DAV-\d{4}-\d{3}-[0-9a-f]{12}","certification_id":r"DCE-\d{4}-\d{3}","blocker_id":r"DBL-\d{4}-\d{3}","transition_id":r"DBT-\d{4}-\d{3}","source_registration_id":r"SRC-\d{4}-\d{3}","license_review_id":r"LRV-\d{4}-\d{3}","projection_id":r"PRJ-\d{4}-\d{3}","packet_signature_id":r"PSG-\d{4}-\d{3}","replay_receipt_id":r"RRC-\d{4}-\d{3}","route_id":r"SRT-\d{4}-\d{3}","packet_id":r"(?:DRP|EGP)-\d{4}-\d{3}","event_id":r"DABL-EVT-\d{4}-\d{6}","license_id":r"LIC-\d{4}-\d{3}","revocation_id":r"REV-\d{4}-\d{3}","supersession_id":r"SUP-\d{4}-\d{3}","replay_id":r"RPL-\d{4}-\d{3}","status_id":r"DST-\d{4}-\d{3}","census_id":r"CENSUS-\d{4}-\d{3}","qs004_id":r"QS004-\d{4}-\d{3}"}
_P = {k:re.compile("^{}$".format(v)) for k,v in _ID.items()}
_LANE=re.compile(r"^LANE-HYP-\d{4}-\d{3}$")
_DABL_CANONICAL_PATH=re.compile(r"^outputs/research/alpha_lab/data_foundry/ledger/[0-9a-f]{64}$")
_FACTS = ("license","pit","universe","delisting","corporate_action","revision","missingness","freshness")
_FORBIDDEN = frozenset({"approved","authenticated","ratified","activate","run_evaluator","run_frozen_evaluator","alpha_claim","lifecycle_claim","challenge_access","holdout_access","promotion","allocation","execution"})

class AuthorityState(str, Enum): DRAFT_NONCANONICAL_PHASE1_BLOCKED="DRAFT_NONCANONICAL_PHASE1_BLOCKED"
class ExternalVerificationState(str, Enum): EXTERNAL_VERIFIER_REQUIRED="EXTERNAL_VERIFIER_REQUIRED"
class AssetClass(str, Enum): RAW="RAW"; DERIVED="DERIVED"; REFERENCE="REFERENCE"
class DataTier(str, Enum): A="TIER_A_DECISION_GRADE"; B="TIER_B_EXPLORATORY"; C="TIER_C_BLOCKED"
class SourceRouteKind(str, Enum): OWNED_FREE="OWNED_FREE"; LICENSED_TRIAL="LICENSED_TRIAL"; PAID="PAID"; SELF_COLLECTED="SELF_COLLECTED"; BOUNDED_PROXY="BOUNDED_PROXY"
class FactStatus(str, Enum): EVIDENCED="EVIDENCED"; NOT_APPLICABLE="NOT_APPLICABLE"; MISSING="MISSING"
class InputImmutability(str, Enum): CREATE_ONLY_IMMUTABLE="CREATE_ONLY_IMMUTABLE"
class CertificationStatus(str, Enum): DRAFT_UNVERIFIED="DRAFT_UNVERIFIED"; BLOCKED="BLOCKED"; REVOKED="REVOKED"; SUPERSEDED="SUPERSEDED"
class BlockerCategory(str, Enum):
    LICENSE="LICENSE"; PIT="PIT"; COVERAGE="COVERAGE"; SEMANTIC="SEMANTIC"; LINEAGE="LINEAGE"; ACCESS="ACCESS"; FRESHNESS="FRESHNESS"; REPLAY="REPLAY"
    UNIVERSE="UNIVERSE"; DELISTING="DELISTING"; CORPORATE_ACTION="CORPORATE_ACTION"; REVISION="REVISION"; MISSINGNESS="MISSINGNESS"; ENTITY_IDENTITY="ENTITY_IDENTITY"; SOURCE_REGISTRATION="SOURCE_REGISTRATION"; ROUTE_LICENSE_REVIEW="ROUTE_LICENSE_REVIEW"; IMMUTABILITY="IMMUTABILITY"; PROVENANCE="PROVENANCE"; CERTIFICATION="CERTIFICATION"; SUPERSESSION="SUPERSESSION"; REVOCATION="REVOCATION"; EXTERNAL_VERIFICATION="EXTERNAL_VERIFICATION"; QS004="QS004"; ACQUISITION="ACQUISITION"; ENTITLEMENT="ENTITLEMENT"; LICENSING="LICENSING"; IDENTITY="IDENTITY"; COLLECTION="COLLECTION"; TRANSFORM="TRANSFORM"; TIMING="TIMING"; POINT_IN_TIME="POINT_IN_TIME"; SURVIVORSHIP="SURVIVORSHIP"; STATISTICAL_POWER="STATISTICAL_POWER"; CONTRACT_DEFECT="CONTRACT_DEFECT"
class BlockerSeverity(str, Enum): CRITICAL="CRITICAL"; HIGH="HIGH"; MEDIUM="MEDIUM"; LOW="LOW"
class BlockerStatus(str, Enum): OPEN="OPEN"; IN_REVIEW="IN_REVIEW"; RESOLVED="RESOLVED"; REOPENED="REOPENED"; SUPERSEDED="SUPERSEDED"
class CandidateDisposition(str, Enum): NO_DATA_ACQUISITION_JUSTIFIED="NO_DATA_ACQUISITION_JUSTIFIED"; EVIDENCE_GAP="EVIDENCE_GAP"; PENDING_OWNER="PENDING_OWNER"
class NextAction(str, Enum): PREPARE_OWNER_REVIEW="PREPARE_OWNER_REVIEW"; REQUEST_QS004="REQUEST_QS004"; REMEDIATE_FACTS="REMEDIATE_FACTS"; STOP="STOP"

def strict_json_loads(text: str)->Any:
    def pairs(xs):
        d={}
        for k,v in xs:
            if k in d: raise ContractValidationError("duplicate JSON key: {}".format(k))
            d[k]=v
        return d
    def finite(v): raise ContractValidationError("non-finite JSON number: {}".format(v))
    try: return json.loads(text, object_pairs_hook=pairs, parse_constant=finite)
    except json.JSONDecodeError as e: raise ContractValidationError("invalid JSON") from e
def freeze(v):
    if isinstance(v,Mapping): return MappingProxyType({str(k):freeze(x) for k,x in v.items()})
    if isinstance(v,(tuple,list)): return tuple(freeze(x) for x in v)
    if isinstance(v,float) and not math.isfinite(v): raise ContractValidationError("non-finite JSON value")
    if v is None or isinstance(v,(str,int,bool,float)): return v
    raise ContractValidationError("unsupported contract value")
def thaw(v):
    if isinstance(v,Mapping): return {str(k):thaw(x) for k,x in v.items()}
    if isinstance(v,(tuple,list)): return [thaw(x) for x in v]
    return v
def _id(v,k):
    if not isinstance(v,str) or _P[k].fullmatch(v) is None: raise ContractValidationError("{} has invalid format".format(k))
    return v
def _s(v,k): require_non_empty(v,k); return v
def _h(v,k): require_sha256(v,k); return v
def _utc(v,k):
    if not isinstance(v,datetime) or v.tzinfo is None or v.utcoffset()!=timezone.utc.utcoffset(v): raise ContractValidationError("{} must be UTC".format(k))
    return v
def parse_utc(v,k):
    if not isinstance(v,str) or not v.endswith("Z"): raise ContractValidationError("{} must be canonical UTC".format(k))
    try: x=datetime.fromisoformat(v[:-1]+"+00:00")
    except ValueError as e: raise ContractValidationError("{} invalid".format(k)) from e
    _utc(x,k)
    if format_datetime(x)!=v: raise ContractValidationError("{} does not round trip canonically".format(k))
    return x
def _enum(c,v,k):
    try:return c(v)
    except (ValueError,TypeError) as e: raise ContractValidationError("{} unsupported".format(k)) from e
def _exact(raw,names,kind):
    if not isinstance(raw,Mapping) or set(raw)!=set(names): raise ContractValidationError("{} has unknown or missing fields".format(kind))
    if _FORBIDDEN.intersection(raw): raise ContractValidationError("{} has forbidden authority field".format(kind))
    return raw
def _tuple(v,k):
    if not isinstance(v,(list,tuple)) or not v or any(not isinstance(x,str) or not x.strip() for x in v) or len(set(v))!=len(v): raise ContractValidationError("{} must be unique nonempty strings".format(k))
    return tuple(v)
def _facts(v):
    if not isinstance(v,Mapping) or set(v)!=set(_FACTS): raise ContractValidationError("facts must include all required facts")
    out={}
    for k,x in v.items():
        x=_exact(x,("status","evidence_sha256","reason","observed_at"),"fact")
        z=_enum(FactStatus,x["status"],"fact status")
        if z is FactStatus.EVIDENCED:
            _h(x["evidence_sha256"],"fact evidence")
            if x["reason"] is not None: raise ContractValidationError("evidenced fact has N/A reason")
        elif x["evidence_sha256"] is not None or not isinstance(x["reason"],str) or x["reason"].strip().lower() in {"","n/a","na","none","unknown","not applicable"}: raise ContractValidationError("missing/N/A fact requires concrete reason")
        out[k]=freeze({"status":z.value,"evidence_sha256":x["evidence_sha256"],"reason":x["reason"],"observed_at":format_datetime(parse_utc(x["observed_at"],"fact observed_at"))})
    return MappingProxyType(out)

@dataclass(frozen=True)
class SignedExportBindingReceipt:
    signed_schema_version:str; signed_classification:str; exported_at:datetime; canonical_event_store:str; source_ledger_receipt_schema_version:str; ledger_bytes_sha256:str; event_count:int; event_chain_head:str|None; head_by_event_count:Mapping[str,str|None]; latest_event_recorded_at:datetime|None; event_chain_replay_verified:bool; typed_semantic_replay_verified:bool; identity_activation_head_hash:str; legacy_prefix_nondecision_grade:bool; projection_sha256:str; unsigned_export_sha256:str; projection_export_hash:str; exporter_identity_sha256:str; active_identity_registry_sha256:str; exporter_attestation_sha256:str; membership_sha256:str; external_verification_state:ExternalVerificationState=ExternalVerificationState.EXTERNAL_VERIFIER_REQUIRED; schema_version:str="caerus_alpha_lab_dabl_signed_export_binding_v3"
    def __post_init__(self):
        if self.signed_schema_version!="caerus_alpha_lab_signed_projection_export_v1" or self.signed_classification!="SIGNED_RESEARCH_ONLY_NON_EXECUTIONAL" or self.source_ledger_receipt_schema_version!="caerus_alpha_lab_source_ledger_receipt_v1":raise ContractValidationError("official signed-export schema/classification mismatch")
        if not isinstance(self.canonical_event_store,str) or not self.canonical_event_store or self.canonical_event_store.startswith(("/","./")) or self.canonical_event_store.endswith("/") or "//" in self.canonical_event_store or any(part in {".",".."} for part in self.canonical_event_store.split("/")) or "\\" in self.canonical_event_store:raise ContractValidationError("canonical_event_store must be normalized repo-relative")
        _utc(self.exported_at,"exported_at")
        if self.latest_event_recorded_at is not None:_utc(self.latest_event_recorded_at,"latest_event_recorded_at")
        if not isinstance(self.event_count,int) or isinstance(self.event_count,bool) or self.event_count<0 or not isinstance(self.head_by_event_count,Mapping) or set(self.head_by_event_count)!={str(i) for i in range(self.event_count+1)}: raise ContractValidationError("source receipt count/ancestry invalid")
        for key,value in self.head_by_event_count.items():
            if value is not None:_h(value,"head_by_event_count")
        if self.event_count==0:
            if self.event_chain_head is not None or self.latest_event_recorded_at is not None or dict(self.head_by_event_count)!={"0":None}:raise ContractValidationError("empty source receipt must have null head/latest")
        else:
            _h(self.event_chain_head,"event_chain_head")
            if self.latest_event_recorded_at is None or self.exported_at<self.latest_event_recorded_at or self.head_by_event_count["0"] is not None or self.head_by_event_count[str(self.event_count)]!=self.event_chain_head:raise ContractValidationError("source receipt chronology/ancestry invalid")
            if any(self.head_by_event_count[str(i)] is None for i in range(1,self.event_count+1)) or len({self.head_by_event_count[str(i)] for i in range(1,self.event_count+1)})!=self.event_count:raise ContractValidationError("nonempty source receipt has incomplete/repeated ancestry")
        for k in ("ledger_bytes_sha256","identity_activation_head_hash","projection_sha256","unsigned_export_sha256","projection_export_hash","exporter_identity_sha256","active_identity_registry_sha256","exporter_attestation_sha256","membership_sha256"): _h(getattr(self,k),k)
        if not all(isinstance(x,bool) for x in (self.event_chain_replay_verified,self.typed_semantic_replay_verified,self.legacy_prefix_nondecision_grade)) or not (self.event_chain_replay_verified and self.typed_semantic_replay_verified and self.legacy_prefix_nondecision_grade):raise ContractValidationError("signed-export receipt verification flags invalid")
        if self.external_verification_state is not ExternalVerificationState.EXTERNAL_VERIFIER_REQUIRED: raise ContractValidationError("local code cannot mint verification")
        object.__setattr__(self,"head_by_event_count",MappingProxyType({str(k):v for k,v in self.head_by_event_count.items()}))
    @property
    def externally_authenticated(self): return False
    def to_dict(self): return {**self.__dict__,"exported_at":format_datetime(self.exported_at),"latest_event_recorded_at":format_datetime(self.latest_event_recorded_at) if self.latest_event_recorded_at else None,"head_by_event_count":dict(self.head_by_event_count),"external_verification_state":self.external_verification_state.value}

@dataclass(frozen=True)
class LedgerReference:
    family_id:str; hypothesis_id:str; experiment_id:str; candidate_id:str; binding_receipt:SignedExportBindingReceipt; external_verification_state:ExternalVerificationState=ExternalVerificationState.EXTERNAL_VERIFIER_REQUIRED; schema_version:str="caerus_alpha_lab_dabl_ledger_reference_v2"
    def __post_init__(self):
        for k in ("family_id","hypothesis_id","experiment_id","candidate_id"): _id(getattr(self,k),k)
        if not isinstance(self.binding_receipt,SignedExportBindingReceipt) or self.external_verification_state is not ExternalVerificationState.EXTERNAL_VERIFIER_REQUIRED: raise ContractValidationError("external signed export binding required")
    @property
    def externally_authenticated(self): return False
    def to_dict(self): return {"schema_version":self.schema_version,"family_id":self.family_id,"hypothesis_id":self.hypothesis_id,"experiment_id":self.experiment_id,"candidate_id":self.candidate_id,"binding_receipt":self.binding_receipt.to_dict(),"external_verification_state":self.external_verification_state.value,"externally_authenticated":False}

@dataclass(frozen=True)
class LicenseTerms:
    license_id:str; provider:str; dataset:str; terms_sha256:str; ai_use_permitted:bool; retention_terms:str; redistribution_terms:str; accepted_at:datetime|None; schema_version:str="caerus_alpha_lab_dabl_license_v1"
    def __post_init__(self):
        _id(self.license_id,"license_id"); [_s(getattr(self,k),k) for k in ("provider","dataset","retention_terms","redistribution_terms")]; _h(self.terms_sha256,"terms_sha256")
        if not isinstance(self.ai_use_permitted,bool): raise ContractValidationError("AI use flag invalid")
        if self.accepted_at is not None:_utc(self.accepted_at,"accepted_at")
    def to_dict(self):return {"schema_version":self.schema_version,"license_id":self.license_id,"provider":self.provider,"dataset":self.dataset,"terms_sha256":self.terms_sha256,"ai_use_permitted":self.ai_use_permitted,"retention_terms":self.retention_terms,"redistribution_terms":self.redistribution_terms,"accepted_at":format_datetime(self.accepted_at) if self.accepted_at else None}
@dataclass(frozen=True)
class SourceRegistration:
    source_registration_id:str;source_provider:str;dataset:str;owner:str;access_contract_sha256:str;registered_at:datetime;schema_version:str="caerus_alpha_lab_dabl_source_registration_v1"
    def __post_init__(self):_id(self.source_registration_id,"source_registration_id");[_s(getattr(self,k),k) for k in ("source_provider","dataset","owner")];_h(self.access_contract_sha256,"access contract");_utc(self.registered_at,"registered at")
    def to_dict(self):return {"schema_version":self.schema_version,"source_registration_id":self.source_registration_id,"source_provider":self.source_provider,"dataset":self.dataset,"owner":self.owner,"access_contract_sha256":self.access_contract_sha256,"registered_at":format_datetime(self.registered_at)}
@dataclass(frozen=True)
class SourceRouteLicenseReview:
    license_review_id:str;route_id:str;source_registration_id:str;source_provider:str;dataset:str;exact_scope:str;route_sha256:str;license_id:str;reviewer_id:str;reviewed_at:datetime;terms_sha256:str;schema_version:str="caerus_alpha_lab_dabl_route_license_review_v2"
    def __post_init__(self):
        _id(self.license_review_id,"license_review_id");_id(self.route_id,"route_id");_id(self.source_registration_id,"source_registration_id");_id(self.license_id,"license_id");[_s(getattr(self,k),k) for k in ("source_provider","dataset","exact_scope","reviewer_id")];_h(self.route_sha256,"route hash");_utc(self.reviewed_at,"reviewed at");_h(self.terms_sha256,"terms")
    def to_dict(self):return {"schema_version":self.schema_version,"license_review_id":self.license_review_id,"route_id":self.route_id,"source_registration_id":self.source_registration_id,"source_provider":self.source_provider,"dataset":self.dataset,"exact_scope":self.exact_scope,"route_sha256":self.route_sha256,"license_id":self.license_id,"reviewer_id":self.reviewer_id,"reviewed_at":format_datetime(self.reviewed_at),"terms_sha256":self.terms_sha256}

@dataclass(frozen=True)
class SourceRoute:
    route_id:str; source_registration_id:str; route_kind:SourceRouteKind; source_provider:str; dataset:str; owner:str; exact_scope:str; external_write_required:bool; contact_required:bool; credential_required:bool; terms_acceptance_required:bool; spend_required:bool; owner_authorization_required:bool; estimated_cost_usd:float|None; estimated_effort_hours:float|None; equivalence_to_required_asset:str; residual_claim:str; schema_version:str="caerus_alpha_lab_dabl_source_route_v3"
    def __post_init__(self):
        _id(self.route_id,"route_id");_id(self.source_registration_id,"source_registration_id");
        if not isinstance(self.route_kind,SourceRouteKind):raise ContractValidationError("route kind invalid")
        [_s(getattr(self,k),k) for k in ("source_provider","dataset","owner","exact_scope","equivalence_to_required_asset","residual_claim")]
        if not all(isinstance(x,bool) for x in (self.external_write_required,self.contact_required,self.credential_required,self.terms_acceptance_required,self.spend_required,self.owner_authorization_required)):raise ContractValidationError("route flags invalid")
        if any((self.external_write_required,self.contact_required,self.credential_required,self.terms_acceptance_required,self.spend_required)) and not self.owner_authorization_required:raise ContractValidationError("every external route action requires owner authorization")
        if self.route_kind is SourceRouteKind.SELF_COLLECTED and not self.external_write_required:raise ContractValidationError("self collected requires external write authority")
        if self.route_kind in {SourceRouteKind.PAID,SourceRouteKind.LICENSED_TRIAL} and not(self.contact_required and self.terms_acceptance_required and self.spend_required):raise ContractValidationError("licensed/paid needs owner authority")
        for x,k in ((self.estimated_cost_usd,"cost"),(self.estimated_effort_hours,"effort")):
            if x is not None and(not isinstance(x,(int,float)) or not math.isfinite(x) or x<0):raise ContractValidationError("{} invalid".format(k))
        if self.route_kind is SourceRouteKind.BOUNDED_PROXY and "cannot" not in self.residual_claim.lower():raise ContractValidationError("proxy must state residual limitation")
    def to_dict(self):return {**self.__dict__,"route_kind":self.route_kind.value}

@dataclass(frozen=True)
class DataAssetDefinition:
    asset_definition_id:str; name:str; asset_class:AssetClass; asset_role:str; required_claim_scope:str; source_registration_id:str; source_provider:str; dataset:str; owner:str; ledger_reference:LedgerReference; source_routes:Tuple[SourceRoute,...]; dependent_lane_ids:Tuple[str,...]; schema_version:str="caerus_alpha_lab_data_asset_definition_v3"
    def __post_init__(self):
        _id(self.asset_definition_id,"asset_definition_id");_id(self.source_registration_id,"source_registration_id"); [_s(getattr(self,k),k) for k in ("name","asset_role","required_claim_scope","source_provider","dataset","owner")]
        if not isinstance(self.asset_class,AssetClass) or not isinstance(self.ledger_reference,LedgerReference) or not self.source_routes or any(not isinstance(x,SourceRoute) for x in self.source_routes) or len({x.route_id for x in self.source_routes})!=len(self.source_routes):raise ContractValidationError("definition binding/routes invalid")
        object.__setattr__(self,"source_routes",tuple(self.source_routes));object.__setattr__(self,"dependent_lane_ids",_tuple(self.dependent_lane_ids,"dependent lane IDs"))
    def to_dict(self):return {"schema_version":self.schema_version,"asset_definition_id":self.asset_definition_id,"name":self.name,"asset_class":self.asset_class.value,"asset_role":self.asset_role,"required_claim_scope":self.required_claim_scope,"source_registration_id":self.source_registration_id,"source_provider":self.source_provider,"dataset":self.dataset,"owner":self.owner,"ledger_reference":self.ledger_reference.to_dict(),"source_routes":[x.to_dict() for x in self.source_routes],"dependent_lane_ids":list(self.dependent_lane_ids)}

@dataclass(frozen=True)
class DataAssetVersion:
    asset_version_id:str; asset_definition_id:str; source_registration_id:str; source_route_id:str; immutable_bundle_sha256:str; immutable_manifest_sha256:str; file_inventory_sha256:str; upstream_version_sha256s:Mapping[str,str]; input_immutability:InputImmutability; license_terms:LicenseTerms; coverage_contract_sha256:str; entity_key_contract_sha256:str; entity_history_sha256:str; availability_at:datetime; effective_at:datetime; retrieved_at:datetime; ingested_at:datetime; model_available_at:datetime; facts:Mapping[str,Mapping[str,Any]]; dependent_lane_ids:Tuple[str,...]; schema_version:str="caerus_alpha_lab_data_asset_version_v3"
    def __post_init__(self):
        _id(self.asset_version_id,"asset_version_id");_id(self.asset_definition_id,"asset_definition_id");_id(self.source_registration_id,"source_registration_id");_id(self.source_route_id,"route_id");[_h(getattr(self,k),k) for k in ("immutable_bundle_sha256","immutable_manifest_sha256","file_inventory_sha256","coverage_contract_sha256","entity_key_contract_sha256","entity_history_sha256")]
        if self.input_immutability is not InputImmutability.CREATE_ONLY_IMMUTABLE:raise ContractValidationError("mutable/staging/checkpoint/latest forbidden")
        if not isinstance(self.license_terms,LicenseTerms) or not isinstance(self.upstream_version_sha256s,Mapping) or not self.upstream_version_sha256s:raise ContractValidationError("license/upstream required")
        object.__setattr__(self,"upstream_version_sha256s",MappingProxyType({str(k):_h(v,"upstream hash") for k,v in self.upstream_version_sha256s.items()}))
        [_utc(getattr(self,k),k) for k in ("availability_at","effective_at","retrieved_at","ingested_at","model_available_at")]
        if not(self.availability_at<=self.retrieved_at<=self.ingested_at<=self.model_available_at) or self.effective_at>self.model_available_at:raise ContractValidationError("temporal causality invalid")
        object.__setattr__(self,"facts",_facts(self.facts));object.__setattr__(self,"dependent_lane_ids",_tuple(self.dependent_lane_ids,"dependent lanes"))
    def to_dict(self):return {"schema_version":self.schema_version,"asset_version_id":self.asset_version_id,"asset_definition_id":self.asset_definition_id,"source_registration_id":self.source_registration_id,"source_route_id":self.source_route_id,"immutable_bundle_sha256":self.immutable_bundle_sha256,"immutable_manifest_sha256":self.immutable_manifest_sha256,"file_inventory_sha256":self.file_inventory_sha256,"upstream_version_sha256s":dict(self.upstream_version_sha256s),"input_immutability":self.input_immutability.value,"license_terms":self.license_terms.to_dict(),"coverage_contract_sha256":self.coverage_contract_sha256,"entity_key_contract_sha256":self.entity_key_contract_sha256,"entity_history_sha256":self.entity_history_sha256,"availability_at":format_datetime(self.availability_at),"effective_at":format_datetime(self.effective_at),"retrieved_at":format_datetime(self.retrieved_at),"ingested_at":format_datetime(self.ingested_at),"model_available_at":format_datetime(self.model_available_at),"facts":thaw(self.facts),"dependent_lane_ids":list(self.dependent_lane_ids)}

@dataclass(frozen=True)
class RevocationRecord:
    revocation_id:str; asset_version_id:str; reason:str; revoked_at:datetime; evidence_sha256:str; schema_version:str="caerus_alpha_lab_dabl_revocation_v1"
    def __post_init__(self):_id(self.revocation_id,"revocation_id");_id(self.asset_version_id,"asset_version_id");_s(self.reason,"reason");_utc(self.revoked_at,"revoked_at");_h(self.evidence_sha256,"evidence")
    def to_dict(self):return {"schema_version":self.schema_version,"revocation_id":self.revocation_id,"asset_version_id":self.asset_version_id,"reason":self.reason,"revoked_at":format_datetime(self.revoked_at),"evidence_sha256":self.evidence_sha256}
@dataclass(frozen=True)
class SupersessionRecord:
    supersession_id:str; prior_asset_version_id:str; replacement_asset_version_id:str; reason:str; recorded_at:datetime; schema_version:str="caerus_alpha_lab_dabl_supersession_v1"
    def __post_init__(self):
        _id(self.supersession_id,"supersession_id");_id(self.prior_asset_version_id,"asset_version_id");_id(self.replacement_asset_version_id,"asset_version_id");_s(self.reason,"reason");_utc(self.recorded_at,"recorded_at")
        if self.prior_asset_version_id==self.replacement_asset_version_id:raise ContractValidationError("self supersession")
    def to_dict(self):return {"schema_version":self.schema_version,"supersession_id":self.supersession_id,"prior_asset_version_id":self.prior_asset_version_id,"replacement_asset_version_id":self.replacement_asset_version_id,"reason":self.reason,"recorded_at":format_datetime(self.recorded_at)}
@dataclass(frozen=True)
class IndependentReplayRequirement:
    replay_id:str; asset_version_id:str; replay_plan_sha256:str; independent_reviewer_id:str; requested_at:datetime; completed_at:datetime|None; replay_receipt_sha256:str|None; schema_version:str="caerus_alpha_lab_dabl_replay_v2"
    def __post_init__(self):
        _id(self.replay_id,"replay_id");_id(self.asset_version_id,"asset_version_id");_h(self.replay_plan_sha256,"replay plan");_s(self.independent_reviewer_id,"reviewer");_utc(self.requested_at,"requested at")
        # Requirements are prospective. Completion is evidenced only by a later typed receipt.
        if self.completed_at is not None or self.replay_receipt_sha256 is not None:raise ContractValidationError("replay completion belongs in typed receipt")
    def to_dict(self):return {"schema_version":self.schema_version,"replay_id":self.replay_id,"asset_version_id":self.asset_version_id,"replay_plan_sha256":self.replay_plan_sha256,"independent_reviewer_id":self.independent_reviewer_id,"requested_at":format_datetime(self.requested_at),"completed_at":format_datetime(self.completed_at) if self.completed_at else None,"replay_receipt_sha256":self.replay_receipt_sha256}
@dataclass(frozen=True)
class IndependentReplayReceipt:
    replay_receipt_id:str;replay_id:str;asset_version_id:str;raw_manifest_sha256:str;transform_code_sha256:str;runtime_receipt_sha256:str;output_manifest_sha256:str;stratified_sample_sha256:str;discrepancy_report_sha256:str;dabl_head_hash:str;producer_identity_id:str;reviewer_identity_id:str;completed_at:datetime;schema_version:str="caerus_alpha_lab_dabl_independent_replay_receipt_v1"
    def __post_init__(self):
        _id(self.replay_receipt_id,"replay_receipt_id");_id(self.replay_id,"replay_id");_id(self.asset_version_id,"asset_version_id");[_h(getattr(self,k),k) for k in ("raw_manifest_sha256","transform_code_sha256","runtime_receipt_sha256","output_manifest_sha256","stratified_sample_sha256","discrepancy_report_sha256","dabl_head_hash")];_s(self.producer_identity_id,"producer");_s(self.reviewer_identity_id,"reviewer");_utc(self.completed_at,"completed at")
        if self.producer_identity_id==self.reviewer_identity_id:raise ContractValidationError("replay producer/reviewer must differ")
    def to_dict(self):return {"schema_version":self.schema_version,"replay_receipt_id":self.replay_receipt_id,"replay_id":self.replay_id,"asset_version_id":self.asset_version_id,"raw_manifest_sha256":self.raw_manifest_sha256,"transform_code_sha256":self.transform_code_sha256,"runtime_receipt_sha256":self.runtime_receipt_sha256,"output_manifest_sha256":self.output_manifest_sha256,"stratified_sample_sha256":self.stratified_sample_sha256,"discrepancy_report_sha256":self.discrepancy_report_sha256,"dabl_head_hash":self.dabl_head_hash,"producer_identity_id":self.producer_identity_id,"reviewer_identity_id":self.reviewer_identity_id,"completed_at":format_datetime(self.completed_at)}
@dataclass(frozen=True)
class SignedProjectionEvent:
    projection_id:str;binding_receipt:SignedExportBindingReceipt;projection_sha256:str;signed_at:datetime;schema_version:str="caerus_alpha_lab_dabl_signed_projection_v1"
    def __post_init__(self):
        _id(self.projection_id,"projection_id");_h(self.projection_sha256,"projection");_utc(self.signed_at,"signed at")
        if not isinstance(self.binding_receipt,SignedExportBindingReceipt) or self.projection_sha256!=self.binding_receipt.projection_sha256:raise ContractValidationError("projection binding/hash invalid")
    def to_dict(self):return {"schema_version":self.schema_version,"projection_id":self.projection_id,"binding_receipt":self.binding_receipt.to_dict(),"projection_sha256":self.projection_sha256,"signed_at":format_datetime(self.signed_at)}
@dataclass(frozen=True)
class SignedPacketEvent:
    packet_signature_id:str;packet_id:str;packet_sha256:str;signer_identity_id:str;signature_sha256:str;binding_receipt:SignedExportBindingReceipt;external_verification_state:ExternalVerificationState;signed_at:datetime;schema_version:str="caerus_alpha_lab_dabl_signed_packet_v2"
    def __post_init__(self):
        _id(self.packet_signature_id,"packet_signature_id");_id(self.packet_id,"packet_id");_h(self.packet_sha256,"packet");_s(self.signer_identity_id,"signer");_h(self.signature_sha256,"signature");_utc(self.signed_at,"signed at")
        if not isinstance(self.binding_receipt,SignedExportBindingReceipt) or self.external_verification_state is not ExternalVerificationState.EXTERNAL_VERIFIER_REQUIRED:raise ContractValidationError("signed packet cannot gain local authority")
    @property
    def externally_authenticated(self):return False
    def to_dict(self):return {"schema_version":self.schema_version,"packet_signature_id":self.packet_signature_id,"packet_id":self.packet_id,"packet_sha256":self.packet_sha256,"signer_identity_id":self.signer_identity_id,"signature_sha256":self.signature_sha256,"binding_receipt":self.binding_receipt.to_dict(),"external_verification_state":self.external_verification_state.value,"signed_at":format_datetime(self.signed_at),"externally_authenticated":False}
@dataclass(frozen=True)
class DataCertification:
    certification_id:str; asset_version_id:str; tier:DataTier; status:CertificationStatus; certifier_id:str; signature_sha256:str|None; certified_at:datetime; ledger_reference:LedgerReference; certification_basis_sha256:str; independent_replay:IndependentReplayRequirement; independent_replay_receipt_sha256:str|None; freshness_deadline:datetime; rationale:str; schema_version:str="caerus_alpha_lab_data_certification_v3"
    def __post_init__(self):
        _id(self.certification_id,"certification_id");_id(self.asset_version_id,"asset_version_id")
        if not isinstance(self.tier,DataTier) or not isinstance(self.status,CertificationStatus) or not isinstance(self.ledger_reference,LedgerReference) or not isinstance(self.independent_replay,IndependentReplayRequirement):raise ContractValidationError("certification types invalid")
        if self.status is CertificationStatus.DRAFT_UNVERIFIED and self.signature_sha256 is not None:raise ContractValidationError("draft cannot contain signature")
        if self.signature_sha256:_h(self.signature_sha256,"signature")
        _s(self.certifier_id,"certifier");_utc(self.certified_at,"certified at");_h(self.certification_basis_sha256,"basis");_utc(self.freshness_deadline,"freshness deadline");_s(self.rationale,"rationale")
        if self.freshness_deadline<=self.certified_at or self.independent_replay.asset_version_id!=self.asset_version_id:raise ContractValidationError("certification temporal/replay invalid")
        if self.tier is DataTier.A:
            _h(self.independent_replay_receipt_sha256,"independent replay receipt")
        elif self.independent_replay_receipt_sha256 is not None:_h(self.independent_replay_receipt_sha256,"independent replay receipt")
    def to_dict(self):return {"schema_version":self.schema_version,"certification_id":self.certification_id,"asset_version_id":self.asset_version_id,"tier":self.tier.value,"status":self.status.value,"certifier_id":self.certifier_id,"signature_sha256":self.signature_sha256,"certified_at":format_datetime(self.certified_at),"ledger_reference":self.ledger_reference.to_dict(),"certification_basis_sha256":self.certification_basis_sha256,"independent_replay":self.independent_replay.to_dict(),"independent_replay_receipt_sha256":self.independent_replay_receipt_sha256,"freshness_deadline":format_datetime(self.freshness_deadline),"rationale":self.rationale}
@dataclass(frozen=True)
class DataBlocker:
    blocker_id:str; asset_definition_id:str; ledger_reference:LedgerReference; blocked_claim:str; category:BlockerCategory; owner:str; severity:BlockerSeverity; status:BlockerStatus; missing_facts:Tuple[str,...]; blocker_reason:str; recommended_action:str; route_ids:Tuple[str,...]; estimated_cost_usd:float|None; estimated_effort_hours:float|None; acceptance_test:str; review_by:datetime; resolution_evidence_sha256:str|None; created_at:datetime; schema_version:str="caerus_alpha_lab_data_blocker_v2"
    def __post_init__(self):
        _id(self.blocker_id,"blocker_id");_id(self.asset_definition_id,"asset_definition_id")
        if not isinstance(self.ledger_reference,LedgerReference) or not isinstance(self.category,BlockerCategory) or not isinstance(self.severity,BlockerSeverity) or not isinstance(self.status,BlockerStatus):raise ContractValidationError("blocker types invalid")
        [_s(getattr(self,k),k) for k in ("blocked_claim","owner","blocker_reason","recommended_action","acceptance_test")]
        missing=_tuple(self.missing_facts,"missing facts")
        if not set(missing).issubset(_FACTS):raise ContractValidationError("invalid missing fact")
        object.__setattr__(self,"missing_facts",missing);routes=_tuple(self.route_ids,"route IDs");[_id(x,"route_id") for x in routes];object.__setattr__(self,"route_ids",routes)
        for x,k in ((self.estimated_cost_usd,"cost"),(self.estimated_effort_hours,"effort")):
            if x is not None and(not isinstance(x,(int,float)) or not math.isfinite(x) or x<0):raise ContractValidationError("{} invalid".format(k))
        _utc(self.review_by,"review by");_utc(self.created_at,"created at")
        if self.review_by<self.created_at:raise ContractValidationError("review precedes creation")
        if self.status is BlockerStatus.RESOLVED:_h(self.resolution_evidence_sha256,"resolution evidence")
        elif self.resolution_evidence_sha256 is not None:raise ContractValidationError("open blocker resolution evidence")
    def to_dict(self):return {"schema_version":self.schema_version,"blocker_id":self.blocker_id,"asset_definition_id":self.asset_definition_id,"ledger_reference":self.ledger_reference.to_dict(),"blocked_claim":self.blocked_claim,"category":self.category.value,"owner":self.owner,"severity":self.severity.value,"status":self.status.value,"missing_facts":list(self.missing_facts),"blocker_reason":self.blocker_reason,"recommended_action":self.recommended_action,"route_ids":list(self.route_ids),"estimated_cost_usd":self.estimated_cost_usd,"estimated_effort_hours":self.estimated_effort_hours,"acceptance_test":self.acceptance_test,"review_by":format_datetime(self.review_by),"resolution_evidence_sha256":self.resolution_evidence_sha256,"created_at":format_datetime(self.created_at)}
@dataclass(frozen=True)
class BlockerTransition:
    transition_id:str;blocker_id:str;from_status:BlockerStatus;to_status:BlockerStatus;reason:str;transitioned_at:datetime;resolution_evidence_sha256:str|None=None;schema_version:str="caerus_alpha_lab_dabl_blocker_transition_v2"
    def __post_init__(self):
        _id(self.transition_id,"transition_id");_id(self.blocker_id,"blocker_id");_s(self.reason,"transition reason");_utc(self.transitioned_at,"transition time")
        if not isinstance(self.from_status,BlockerStatus) or not isinstance(self.to_status,BlockerStatus) or self.from_status==self.to_status:raise ContractValidationError("blocker transition invalid")
        allowed={BlockerStatus.OPEN:{BlockerStatus.IN_REVIEW,BlockerStatus.RESOLVED,BlockerStatus.SUPERSEDED},BlockerStatus.IN_REVIEW:{BlockerStatus.RESOLVED,BlockerStatus.REOPENED,BlockerStatus.SUPERSEDED},BlockerStatus.RESOLVED:{BlockerStatus.REOPENED},BlockerStatus.REOPENED:{BlockerStatus.IN_REVIEW,BlockerStatus.RESOLVED,BlockerStatus.SUPERSEDED},BlockerStatus.SUPERSEDED:set()}
        if self.to_status not in allowed[self.from_status]:raise ContractValidationError("illegal blocker state transition")
        if self.to_status is BlockerStatus.RESOLVED:_h(self.resolution_evidence_sha256,"resolution evidence")
        elif self.resolution_evidence_sha256 is not None:raise ContractValidationError("only resolution carries evidence")
    def to_dict(self):return {"schema_version":self.schema_version,"transition_id":self.transition_id,"blocker_id":self.blocker_id,"from_status":self.from_status.value,"to_status":self.to_status.value,"reason":self.reason,"transitioned_at":format_datetime(self.transitioned_at),"resolution_evidence_sha256":self.resolution_evidence_sha256}
@dataclass(frozen=True)
class DataStatusRecord:
    status_id:str;asset_version_id:str;current_status:CertificationStatus;observed_at:datetime;reason:str;revocation_id:str|None;supersession_id:str|None;schema_version:str="caerus_alpha_lab_dabl_status_v1"
    def __post_init__(self):
        _id(self.status_id,"status_id");_id(self.asset_version_id,"asset_version_id");_utc(self.observed_at,"observed at");_s(self.reason,"reason")
        if not isinstance(self.current_status,CertificationStatus):raise ContractValidationError("status invalid")
        if self.current_status is CertificationStatus.REVOKED:_id(self.revocation_id,"revocation_id")
        elif self.revocation_id is not None:raise ContractValidationError("unexpected revocation")
        if self.current_status is CertificationStatus.SUPERSEDED:_id(self.supersession_id,"supersession_id")
        elif self.supersession_id is not None:raise ContractValidationError("unexpected supersession")
    def to_dict(self):return {"schema_version":self.schema_version,"status_id":self.status_id,"asset_version_id":self.asset_version_id,"current_status":self.current_status.value,"observed_at":format_datetime(self.observed_at),"reason":self.reason,"revocation_id":self.revocation_id,"supersession_id":self.supersession_id}
@dataclass(frozen=True)
class DataReadinessPacket:
    packet_id:str;authority_state:AuthorityState;ledger_reference:LedgerReference;asset_definition_ids:Tuple[str,...];certification_ids:Tuple[str,...];tier:DataTier;permitted_next_action:NextAction;created_at:datetime;schema_version:str="caerus_alpha_lab_data_readiness_packet_v2"
    def __post_init__(self):
        _id(self.packet_id,"packet_id")
        if not self.packet_id.startswith("DRP-") or self.authority_state is not AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED or not isinstance(self.ledger_reference,LedgerReference) or not isinstance(self.tier,DataTier) or not isinstance(self.permitted_next_action,NextAction):raise ContractValidationError("readiness authority invalid")
        ds=_tuple(self.asset_definition_ids,"asset IDs");cs=_tuple(self.certification_ids,"cert IDs");[_id(x,"asset_definition_id") for x in ds];[_id(x,"certification_id") for x in cs];object.__setattr__(self,"asset_definition_ids",ds);object.__setattr__(self,"certification_ids",cs);_utc(self.created_at,"created at")
    @property
    def frozen_evaluator_permitted(self):return False
    @property
    def alpha_or_lifecycle_claim_permitted(self):return False
    def to_dict(self):return {"schema_version":self.schema_version,"packet_id":self.packet_id,"authority_state":self.authority_state.value,"ledger_reference":self.ledger_reference.to_dict(),"asset_definition_ids":list(self.asset_definition_ids),"certification_ids":list(self.certification_ids),"tier":self.tier.value,"permitted_next_action":self.permitted_next_action.value,"created_at":format_datetime(self.created_at),"frozen_evaluator_permitted":False,"alpha_or_lifecycle_claim_permitted":False}
@dataclass(frozen=True)
class EvidenceGapPacket:
    packet_id:str;authority_state:AuthorityState;ledger_reference:LedgerReference;blocker_ids:Tuple[str,...];source_routes:Tuple[SourceRoute,...];tier:DataTier;disposition:CandidateDisposition;permitted_next_action:NextAction;created_at:datetime;schema_version:str="caerus_alpha_lab_evidence_gap_packet_v2"
    def __post_init__(self):
        _id(self.packet_id,"packet_id")
        if not self.packet_id.startswith("EGP-") or self.authority_state is not AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED or not isinstance(self.ledger_reference,LedgerReference) or not isinstance(self.tier,DataTier) or not isinstance(self.disposition,CandidateDisposition) or not isinstance(self.permitted_next_action,NextAction):raise ContractValidationError("gap authority invalid")
        bs=_tuple(self.blocker_ids,"blocker IDs");[_id(x,"blocker_id") for x in bs]
        if not self.source_routes or any(not isinstance(x,SourceRoute) for x in self.source_routes):raise ContractValidationError("gap routes invalid")
        object.__setattr__(self,"blocker_ids",bs);object.__setattr__(self,"source_routes",tuple(self.source_routes));_utc(self.created_at,"created at")
    @property
    def frozen_evaluator_permitted(self):return False
    @property
    def alpha_or_lifecycle_claim_permitted(self):return False
    def to_dict(self):return {"schema_version":self.schema_version,"packet_id":self.packet_id,"authority_state":self.authority_state.value,"ledger_reference":self.ledger_reference.to_dict(),"blocker_ids":list(self.blocker_ids),"source_routes":[x.to_dict() for x in self.source_routes],"tier":self.tier.value,"disposition":self.disposition.value,"permitted_next_action":self.permitted_next_action.value,"created_at":format_datetime(self.created_at),"frozen_evaluator_permitted":False,"alpha_or_lifecycle_claim_permitted":False}
@dataclass(frozen=True)
class EntryCensus:
    census_id:str;authority_state:AuthorityState;phase1_tag:str;phase1_release_sha256:str;phase1_runtime_receipt_sha256:str;schema_manifest_sha256:str;registry_head_hash:str;binding_receipt:SignedExportBindingReceipt;binding_receipt_sha256:str;canonical_paths:Tuple[str,...];rollback_reference:str;lane_to_asset_ids:Mapping[str,Tuple[str,...]];candidate_dispositions:Mapping[str,CandidateDisposition];candidate_viable_untried_route_ids:Mapping[str,Tuple[str,...]];non_data_authority_dispositions:Mapping[str,str];dynamic_derivation_receipt_sha256:str;zero_spend:bool;zero_vendor:bool;zero_credentials:bool;zero_external_write:bool;zero_cloud:bool;zero_iam:bool;zero_terms:bool;zero_holdout:bool;zero_scheduler:bool;zero_lifecycle:bool;zero_trading:bool;zero_broker:bool;zero_capital:bool;separate_gcp_initialization_required:bool;schema_version:str="caerus_alpha_lab_dabl_entry_census_v2"
    def __post_init__(self):
        _id(self.census_id,"census_id")
        if self.authority_state is not AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED:raise ContractValidationError("census must remain blocked")
        _s(self.phase1_tag,"phase1 tag");[_h(getattr(self,k),k) for k in ("phase1_release_sha256","phase1_runtime_receipt_sha256","schema_manifest_sha256","registry_head_hash","binding_receipt_sha256","dynamic_derivation_receipt_sha256")]
        if self.schema_manifest_sha256!=SCHEMA_MANIFEST_SHA256 or not isinstance(self.binding_receipt,SignedExportBindingReceipt) or self.binding_receipt.event_chain_head is None or self.registry_head_hash!=self.binding_receipt.event_chain_head or self.binding_receipt_sha256!=canonical_hash(self.binding_receipt.to_dict()):raise ContractValidationError("census must bind exact schema, registry head, and signed-export receipt")
        paths=_tuple(self.canonical_paths,"canonical paths")
        if any(_DABL_CANONICAL_PATH.fullmatch(path) is None for path in paths):raise ContractValidationError("noncanonical DABL path")
        object.__setattr__(self,"canonical_paths",paths);_s(self.rollback_reference,"rollback")
        if not isinstance(self.lane_to_asset_ids,Mapping) or len(self.lane_to_asset_ids)!=13:raise ContractValidationError("census must map exactly 13 lanes")
        lanes={str(k):_tuple(v,"asset IDs") for k,v in self.lane_to_asset_ids.items()};assets=[x for vs in lanes.values() for x in vs]
        if any(_LANE.fullmatch(key) is None for key in lanes) or len(set(assets))!=21:raise ContractValidationError("lane IDs/asset union invalid")
        [_id(x,"asset_definition_id") for x in assets];object.__setattr__(self,"lane_to_asset_ids",MappingProxyType(lanes))
        if not isinstance(self.candidate_dispositions,Mapping) or not self.candidate_dispositions:raise ContractValidationError("candidate dispositions required")
        dispositions={str(k):_enum(CandidateDisposition,v,"disposition") for k,v in self.candidate_dispositions.items()};[_id(k,"candidate_id") for k in dispositions];object.__setattr__(self,"candidate_dispositions",MappingProxyType(dispositions))
        viable={str(k):tuple(v) for k,v in self.candidate_viable_untried_route_ids.items()};[_id(k,"candidate_id") for k in viable]
        if any(not isinstance(routes,tuple) or len(routes)!=len(set(routes)) or any(not isinstance(route,str) or not route.strip() for route in routes) for routes in viable.values()):raise ContractValidationError("viable route set invalid")
        [[ _id(route,"route_id") for route in routes] for routes in viable.values()]
        nondata={str(k):_s(v,"non-data authority disposition") for k,v in self.non_data_authority_dispositions.items()};[_id(k,"candidate_id") for k in nondata]
        if set(viable)!=set(dispositions) or set(nondata)!=set(dispositions):raise ContractValidationError("candidate disposition/route/non-data keysets inconsistent")
        for candidate,disposition in dispositions.items():
            if disposition is CandidateDisposition.NO_DATA_ACQUISITION_JUSTIFIED and (viable.get(candidate,()) or candidate not in nondata):raise ContractValidationError("no-data disposition requires no viable untried route and non-data authority disposition")
        object.__setattr__(self,"candidate_viable_untried_route_ids",MappingProxyType(viable));object.__setattr__(self,"non_data_authority_dispositions",MappingProxyType(nondata))
        if not all(x is True for x in (self.zero_spend,self.zero_vendor,self.zero_credentials,self.zero_external_write,self.zero_cloud,self.zero_iam,self.zero_terms,self.zero_holdout,self.zero_scheduler,self.zero_lifecycle,self.zero_trading,self.zero_broker,self.zero_capital,self.separate_gcp_initialization_required)):raise ContractValidationError("zero defaults/separate GCP required")
    def to_dict(self):return {"schema_version":self.schema_version,"census_id":self.census_id,"authority_state":self.authority_state.value,"phase1_tag":self.phase1_tag,"phase1_release_sha256":self.phase1_release_sha256,"phase1_runtime_receipt_sha256":self.phase1_runtime_receipt_sha256,"schema_manifest_sha256":self.schema_manifest_sha256,"registry_head_hash":self.registry_head_hash,"binding_receipt":self.binding_receipt.to_dict(),"binding_receipt_sha256":self.binding_receipt_sha256,"canonical_paths":list(self.canonical_paths),"rollback_reference":self.rollback_reference,"lane_to_asset_ids":{k:list(v) for k,v in self.lane_to_asset_ids.items()},"candidate_dispositions":{k:v.value for k,v in self.candidate_dispositions.items()},"candidate_viable_untried_route_ids":{k:list(v) for k,v in self.candidate_viable_untried_route_ids.items()},"non_data_authority_dispositions":dict(self.non_data_authority_dispositions),"dynamic_derivation_receipt_sha256":self.dynamic_derivation_receipt_sha256,"zero_spend":True,"zero_vendor":True,"zero_credentials":True,"zero_external_write":True,"zero_cloud":True,"zero_iam":True,"zero_terms":True,"zero_holdout":True,"zero_scheduler":True,"zero_lifecycle":True,"zero_trading":True,"zero_broker":True,"zero_capital":True,"separate_gcp_initialization_required":True}
@dataclass(frozen=True)
class QS004DecisionContract:
    qs004_id:str;authority_state:AuthorityState;entry_census:EntryCensus;entry_census_canonical_json:str;entry_census_sha256:str;entry_census_bytes_sha256:str;owner_authorization_present:bool;signed_decision_sha256:str|None;schema_version:str="caerus_alpha_lab_dabl_qs004_v3"
    def __post_init__(self):
        _id(self.qs004_id,"qs004_id")
        exact=canonical_json(self.entry_census.to_dict())
        if self.authority_state is not AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED or not isinstance(self.entry_census,EntryCensus) or self.owner_authorization_present or self.signed_decision_sha256 is not None or self.entry_census_canonical_json!=exact or self.entry_census_sha256!=canonical_hash(self.entry_census.to_dict()) or self.entry_census_bytes_sha256!=hashlib.sha256(exact.encode("utf-8")).hexdigest():raise ContractValidationError("QS004 cannot be approved locally or detach exact census bytes")
    def to_dict(self):return {"schema_version":self.schema_version,"qs004_id":self.qs004_id,"authority_state":self.authority_state.value,"entry_census":self.entry_census.to_dict(),"entry_census_canonical_json":self.entry_census_canonical_json,"entry_census_sha256":self.entry_census_sha256,"entry_census_bytes_sha256":self.entry_census_bytes_sha256,"owner_authorization_present":False,"signed_decision_sha256":None}

# Strict decoder implemented by rehydrating from the canonical dict.  It rejects
# unknown fields before any nested object is accepted.
def from_dict(cls, raw):
    if not isinstance(raw,Mapping):raise ContractValidationError("contract must be object")
    names=list(cls.__dataclass_fields__) # dataclass declaration is the v2 schema
    derived=["frozen_evaluator_permitted","alpha_or_lifecycle_claim_permitted"] if cls in (DataReadinessPacket,EvidenceGapPacket) else []
    if cls is LedgerReference:derived.append("externally_authenticated")
    if cls is SignedPacketEvent:derived.append("externally_authenticated")
    raw=_exact(raw,names+derived,cls.__name__)
    if raw.get("schema_version")!=cls.__dataclass_fields__["schema_version"].default:raise ContractValidationError("schema mismatch")
    if cls in (DataReadinessPacket,EvidenceGapPacket) and(raw["frozen_evaluator_permitted"] is not False or raw["alpha_or_lifecycle_claim_permitted"] is not False):raise ContractValidationError("draft permissions must be false")
    if cls is LedgerReference and raw["externally_authenticated"] is not False:raise ContractValidationError("local reference cannot authenticate itself")
    if cls is SignedPacketEvent and raw["externally_authenticated"] is not False:raise ContractValidationError("local packet cannot authenticate itself")
    v={k:raw[k] for k in names if k!="schema_version"}
    enum_fields={"authority_state":AuthorityState,"external_verification_state":ExternalVerificationState,"asset_class":AssetClass,"route_kind":SourceRouteKind,"input_immutability":InputImmutability,"tier":DataTier,"current_status":CertificationStatus,"category":BlockerCategory,"severity":BlockerSeverity,"disposition":CandidateDisposition,"permitted_next_action":NextAction}
    if cls is DataCertification:enum_fields["status"]=CertificationStatus
    if cls is DataBlocker:enum_fields["status"]=BlockerStatus
    if cls is BlockerTransition:enum_fields.update({"from_status":BlockerStatus,"to_status":BlockerStatus})
    for k,c in enum_fields.items():
        if k in v:v[k]=_enum(c,v[k],k)
    for k in ("availability_at","effective_at","retrieved_at","ingested_at","model_available_at","requested_at","certified_at","freshness_deadline","review_by","created_at","revoked_at","recorded_at","observed_at","transitioned_at","exported_at","registered_at","reviewed_at","signed_at"):
        if k in v:v[k]=parse_utc(v[k],k)
    for k in ("accepted_at","completed_at","latest_event_recorded_at"):
        if k in v and v[k] is not None:v[k]=parse_utc(v[k],k)
    nested={"binding_receipt":SignedExportBindingReceipt,"ledger_reference":LedgerReference,"license_terms":LicenseTerms,"independent_replay":IndependentReplayRequirement,"entry_census":EntryCensus}
    for k,c in nested.items():
        if k in v:v[k]=c.from_dict(v[k])
    if "source_routes" in v:v["source_routes"]=tuple(SourceRoute.from_dict(x) for x in v["source_routes"])
    if "candidate_dispositions" in v:v["candidate_dispositions"]={k:_enum(CandidateDisposition,x,"disposition") for k,x in v["candidate_dispositions"].items()}
    if "lane_to_asset_ids" in v:v["lane_to_asset_ids"]={k:tuple(x) for k,x in v["lane_to_asset_ids"].items()}
    for k in ("dependent_lane_ids","missing_facts","route_ids","asset_definition_ids","certification_ids","blocker_ids","canonical_paths"):
        if k in v:v[k]=tuple(v[k])
    return cls(**v)
for _c in (SignedExportBindingReceipt,LedgerReference,LicenseTerms,SourceRegistration,SourceRouteLicenseReview,SourceRoute,DataAssetDefinition,DataAssetVersion,RevocationRecord,SupersessionRecord,IndependentReplayRequirement,IndependentReplayReceipt,SignedProjectionEvent,SignedPacketEvent,DataCertification,DataBlocker,BlockerTransition,DataStatusRecord,DataReadinessPacket,EvidenceGapPacket,EntryCensus,QS004DecisionContract):setattr(_c,"from_dict",classmethod(from_dict))

_EVENT_TYPE_TO_CLASS=MappingProxyType({"source_registration":"SourceRegistration","route_license_review":"SourceRouteLicenseReview","asset_definition":"DataAssetDefinition","asset_version":"DataAssetVersion","license_terms":"LicenseTerms","certification":"DataCertification","blocker":"DataBlocker","blocker_transition":"BlockerTransition","revocation":"RevocationRecord","supersession":"SupersessionRecord","replay_requirement":"IndependentReplayRequirement","independent_replay_receipt":"IndependentReplayReceipt","signed_projection":"SignedProjectionEvent","signed_packet":"SignedPacketEvent","status":"DataStatusRecord","readiness_packet":"DataReadinessPacket","evidence_gap_packet":"EvidenceGapPacket","entry_census":"EntryCensus","qs004_decision":"QS004DecisionContract"})
_MANIFEST_CONTRACTS=(SignedExportBindingReceipt,LedgerReference,LicenseTerms,SourceRegistration,SourceRouteLicenseReview,SourceRoute,DataAssetDefinition,DataAssetVersion,RevocationRecord,SupersessionRecord,IndependentReplayRequirement,IndependentReplayReceipt,SignedProjectionEvent,SignedPacketEvent,DataCertification,DataBlocker,BlockerTransition,DataStatusRecord,DataReadinessPacket,EvidenceGapPacket,EntryCensus,QS004DecisionContract)
def _field_manifest(contract):
    return tuple({"name":field.name,"type":str(field.type),"required":field.default is MISSING and field.default_factory is MISSING,"default":"<required>" if field.default is MISSING and field.default_factory is MISSING else "<factory>" if field.default_factory is not MISSING else repr(field.default)} for field in contract.__dataclass_fields__.values())
_DABL_EVENT_FIELDS=("schema_version","event_id","event_type","occurred_at","recorded_at","payload","payload_hash","previous_event_hash","event_hash")
_PROJECTION_FIELDS=("schema_version","authority_state","event_count","event_chain_head","frozen_evaluator_permitted","alpha_or_lifecycle_claim_permitted","asset_current_statuses","certification_current_statuses","blocker_current_statuses","packet_current_validity","packet_current_reasons","source_registrations","route_license_reviews","asset_definitions","asset_versions","licenses","certifications","blockers","blocker_transitions","revocations","supersessions","replays","replay_receipts","signed_projections","signed_packets","statuses","readiness_packets","evidence_gap_packets","entry_censuses","qs004_decisions")
_DABL_EVENT_FIELD_METADATA=(
    {"name":"schema_version","type":"str","required":True,"default":repr("caerus_alpha_lab_dabl_event_v3")},
    {"name":"event_id","type":"str","required":True,"default":"<required>"},
    {"name":"event_type","type":"str","required":True,"default":"<required>"},
    {"name":"occurred_at","type":"UTC RFC3339 str","required":True,"default":"<required>"},
    {"name":"recorded_at","type":"UTC RFC3339 str","required":True,"default":"<required>"},
    {"name":"payload","type":"object","required":True,"default":"<required>"},
    {"name":"payload_hash","type":"SHA256 str","required":True,"default":"<required>"},
    {"name":"previous_event_hash","type":"SHA256 str | None","required":True,"default":"<required>"},
    {"name":"event_hash","type":"SHA256 str","required":True,"default":"<required>"},
)
_PROJECTION_FIELD_TYPES={
    "schema_version":"str","authority_state":"str","event_count":"int","event_chain_head":"SHA256 str | None","frozen_evaluator_permitted":"bool","alpha_or_lifecycle_claim_permitted":"bool","asset_current_statuses":"object[str, CertificationStatus]","certification_current_statuses":"object[str, CertificationStatus]","blocker_current_statuses":"object[str, BlockerStatus]","packet_current_validity":"object[str, bool]","packet_current_reasons":"object[str, str]","source_registrations":"object[str, SourceRegistration]","route_license_reviews":"object[str, SourceRouteLicenseReview]","asset_definitions":"object[str, DataAssetDefinition]","asset_versions":"object[str, DataAssetVersion]","licenses":"object[str, LicenseTerms]","certifications":"object[str, DataCertification]","blockers":"object[str, DataBlocker]","blocker_transitions":"object[str, BlockerTransition]","revocations":"object[str, RevocationRecord]","supersessions":"object[str, SupersessionRecord]","replays":"object[str, IndependentReplayRequirement]","replay_receipts":"object[str, IndependentReplayReceipt]","signed_projections":"object[str, SignedProjectionEvent]","signed_packets":"object[str, SignedPacketEvent]","statuses":"object[str, DataStatusRecord]","readiness_packets":"object[str, DataReadinessPacket]","evidence_gap_packets":"object[str, EvidenceGapPacket]","entry_censuses":"object[str, EntryCensus]","qs004_decisions":"object[str, QS004DecisionContract]",
}
_PROJECTION_FIELD_DEFAULTS={"schema_version":repr("caerus_alpha_lab_dabl_projection_v2"),"authority_state":repr(AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED.value),"event_count":"0","event_chain_head":"None","frozen_evaluator_permitted":"False","alpha_or_lifecycle_claim_permitted":"False"}
_PROJECTION_FIELD_METADATA=tuple({"name":name,"type":_PROJECTION_FIELD_TYPES[name],"required":True,"default":_PROJECTION_FIELD_DEFAULTS.get(name,"{}") } for name in _PROJECTION_FIELDS)
_LITERAL_CONTRACT_FIELDS={c.__name__:tuple(c.__dataclass_fields__) for c in _MANIFEST_CONTRACTS}
_LITERAL_CONTRACT_FIELDS.update({"DABLEvent":_DABL_EVENT_FIELDS,"DABLProjection":_PROJECTION_FIELDS})
_CONTRACT_FIELD_METADATA={c.__name__:_field_manifest(c) for c in _MANIFEST_CONTRACTS}
_CONTRACT_FIELD_METADATA.update({"DABLEvent":_DABL_EVENT_FIELD_METADATA,"DABLProjection":_PROJECTION_FIELD_METADATA})
SCHEMA_MANIFEST_V2=freeze({"schema_version":"caerus_alpha_lab_dabl_schema_manifest_v2","authority_state":AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED.value,"id_regexes":_ID,"literal_contract_fields":_LITERAL_CONTRACT_FIELDS,"contract_field_metadata":_CONTRACT_FIELD_METADATA,"fact_keys":_FACTS,"dabl_event_schema_version":"caerus_alpha_lab_dabl_event_v3","signed_receipt_schema_version":"caerus_alpha_lab_dabl_signed_export_binding_v3","dabl_event_fields":_DABL_EVENT_FIELDS,"dabl_event_field_metadata":_DABL_EVENT_FIELD_METADATA,"projection_fields":_PROJECTION_FIELDS,"projection_field_metadata":_PROJECTION_FIELD_METADATA,"enum_wire_values":{c.__name__:tuple(x.value for x in c) for c in (AuthorityState,ExternalVerificationState,AssetClass,DataTier,SourceRouteKind,FactStatus,InputImmutability,CertificationStatus,BlockerCategory,BlockerSeverity,BlockerStatus,CandidateDisposition,NextAction)},"canonicalization":{"json":"canonical_json","duplicate_keys":"reject","nonfinite":"reject","timestamp":"UTC-Z-exact-round-trip","deep_copy":"freeze/thaw","census_bytes":"utf8 canonical_json sha256"},"event_mapping":tuple(_EVENT_TYPE_TO_CLASS),"event_type_to_class":_EVENT_TYPE_TO_CLASS,"projection_schema":"caerus_alpha_lab_dabl_projection_v2","invariants":{"phase1_blocked":"all packets/projections remain draft and Phase1 blocked","external_verifier_required":"local bindings require external verification and cannot authenticate locally","central_recorded_at_chronology":"all semantic times are at or before recorded_at","all_tier_certification_causality":"every certification/readiness timestamp follows asset, fact, license, replay request, and cited replay receipt times","tier_a_facts_replay_identity":"all eight evidenced facts plus exact independent replay receipt and separated identities","replay_request_causality":"independent replay completes no earlier than asset model availability or its explicit request","transition_status_packet_chronology":"blocker transitions, evidence gaps, asset statuses, and packet signatures cannot backdate their causal predecessors","evidence_gap_route_exactness":"gap routes equal the exact immutable registered-route union cited by their blockers","current_status_freshness":"certification and asset status use deterministic precedence; blocked/revoked/superseded/stale state invalidates current readiness","qs004_exact_census_bytes":"embedded census canonical bytes/hash and materialized lane/assets/routes match"},"source_commit_sha256_placeholder":"0"*64,"runtime_receipt_sha256_placeholder":"0"*64})
SCHEMA_MANIFEST_SHA256=canonical_hash(SCHEMA_MANIFEST_V2)
# This literal is the reviewed pin.  Changing a schema requires an explicit review and pin update.
REVIEWED_SCHEMA_MANIFEST_SHA256="a70874492b8bc58f558e3694eb1af3d146aa629c6623877823e4bb738d661429"
def verify_schema_manifest()->bool:return canonical_hash(SCHEMA_MANIFEST_V2)==SCHEMA_MANIFEST_SHA256==REVIEWED_SCHEMA_MANIFEST_SHA256

# Every direct constructor validates its literal declared version.  This wraps
# each concrete class rather than permitting a generic unversioned base object.
_CONTRACTS=(SignedExportBindingReceipt,LedgerReference,LicenseTerms,SourceRegistration,SourceRouteLicenseReview,SourceRoute,DataAssetDefinition,DataAssetVersion,RevocationRecord,SupersessionRecord,IndependentReplayRequirement,IndependentReplayReceipt,SignedProjectionEvent,SignedPacketEvent,DataCertification,DataBlocker,BlockerTransition,DataStatusRecord,DataReadinessPacket,EvidenceGapPacket,EntryCensus,QS004DecisionContract)
for _contract in _CONTRACTS:
    _original=_contract.__post_init__
    _literal=_contract.__dataclass_fields__["schema_version"].default
    def _versioned(self,_original=_original,_literal=_literal):
        _original(self)
        if self.schema_version!=_literal:raise ContractValidationError("literal schema version mismatch")
    _contract.__post_init__=_versioned
