"""Pure prospective DABL event-plan validation and typed projection.

There is deliberately no canonical or public persistent writer in this module.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Dict, Mapping, Sequence, Tuple
from projects.alpha_lab.factory.canonical import canonical_hash, canonical_json, format_datetime, require_non_empty, require_sha256
from projects.alpha_lab.factory.errors import ContractValidationError, EventStoreIntegrityError
from .models import (AuthorityState, BlockerStatus, DataAssetDefinition, DataAssetVersion, DataBlocker, DataCertification, DataReadinessPacket, DataStatusRecord, EntryCensus, EvidenceGapPacket, ExternalVerificationState, IndependentReplayReceipt, IndependentReplayRequirement, LicenseTerms, QS004DecisionContract, RevocationRecord, SignedPacketEvent, SignedProjectionEvent, SourceRegistration, SourceRouteLicenseReview, SupersessionRecord, BlockerTransition, CertificationStatus, DataTier, FactStatus, _id, _utc, _h, freeze, thaw, parse_utc)

__all__=("DABLEvent","plan_append","project_event_plan")

_EVENTS=MappingProxyType({"source_registration":SourceRegistration,"route_license_review":SourceRouteLicenseReview,"asset_definition":DataAssetDefinition,"asset_version":DataAssetVersion,"license_terms":LicenseTerms,"certification":DataCertification,"blocker":DataBlocker,"blocker_transition":BlockerTransition,"revocation":RevocationRecord,"supersession":SupersessionRecord,"replay_requirement":IndependentReplayRequirement,"independent_replay_receipt":IndependentReplayReceipt,"signed_projection":SignedProjectionEvent,"signed_packet":SignedPacketEvent,"status":DataStatusRecord,"readiness_packet":DataReadinessPacket,"evidence_gap_packet":EvidenceGapPacket,"entry_census":EntryCensus,"qs004_decision":QS004DecisionContract})
_FIELDS=frozenset(("schema_version","event_id","event_type","occurred_at","recorded_at","payload","payload_hash","previous_event_hash","event_hash"))
_PROJECTION_TOKEN=object()

@dataclass(frozen=True)
class DABLEvent:
    event_id:str;event_type:str;occurred_at:datetime;recorded_at:datetime;payload:Mapping[str,Any];payload_hash:str;previous_event_hash:str|None;event_hash:str;schema_version:str="caerus_alpha_lab_dabl_event_v3"
    def __post_init__(self):
        if self.schema_version!="caerus_alpha_lab_dabl_event_v3":raise ContractValidationError("literal DABL event schema mismatch")
        _id(self.event_id,"event_id")
        if self.event_type not in _EVENTS:raise ContractValidationError("unknown DABL event type")
        _utc(self.occurred_at,"occurred_at")
        _utc(self.recorded_at,"recorded_at")
        if self.recorded_at<self.occurred_at:raise ContractValidationError("recorded_at precedes occurred_at")
        object.__setattr__(self,"payload",freeze(self.payload));_h(self.payload_hash,"payload hash");_h(self.event_hash,"event hash")
        if self.previous_event_hash is not None:_h(self.previous_event_hash,"previous hash")
        if canonical_hash(self.payload)!=self.payload_hash:raise ContractValidationError("event payload hash mismatch")
        unsigned={"schema_version":self.schema_version,"event_id":self.event_id,"event_type":self.event_type,"occurred_at":format_datetime(self.occurred_at),"recorded_at":format_datetime(self.recorded_at),"payload":thaw(self.payload),"payload_hash":self.payload_hash,"previous_event_hash":self.previous_event_hash}
        if canonical_hash(unsigned)!=self.event_hash:raise ContractValidationError("event hash mismatch")
    def to_dict(self):return {"schema_version":self.schema_version,"event_id":self.event_id,"event_type":self.event_type,"occurred_at":format_datetime(self.occurred_at),"recorded_at":format_datetime(self.recorded_at),"payload":thaw(self.payload),"payload_hash":self.payload_hash,"previous_event_hash":self.previous_event_hash,"event_hash":self.event_hash}
    @classmethod
    def from_dict(cls,raw):
        if not isinstance(raw,Mapping) or set(raw)!=_FIELDS or raw.get("schema_version")!="caerus_alpha_lab_dabl_event_v3":raise EventStoreIntegrityError("event has unknown/missing schema fields")
        try:return cls(raw["event_id"],raw["event_type"],parse_utc(raw["occurred_at"],"occurred_at"),parse_utc(raw["recorded_at"],"recorded_at"),raw["payload"],raw["payload_hash"],raw["previous_event_hash"],raw["event_hash"],raw["schema_version"])
        except (KeyError,ContractValidationError) as e:raise EventStoreIntegrityError("invalid DABL event") from e

@dataclass(frozen=True)
class DABLProjection:
    authority_state:AuthorityState;event_count:int;event_chain_head:str|None;source_registrations:Mapping[str,SourceRegistration];route_license_reviews:Mapping[str,SourceRouteLicenseReview];asset_definitions:Mapping[str,DataAssetDefinition];asset_versions:Mapping[str,DataAssetVersion];licenses:Mapping[str,LicenseTerms];certifications:Mapping[str,DataCertification];blockers:Mapping[str,DataBlocker];blocker_transitions:Mapping[str,BlockerTransition];revocations:Mapping[str,RevocationRecord];supersessions:Mapping[str,SupersessionRecord];replays:Mapping[str,IndependentReplayRequirement];replay_receipts:Mapping[str,IndependentReplayReceipt];signed_projections:Mapping[str,SignedProjectionEvent];signed_packets:Mapping[str,SignedPacketEvent];statuses:Mapping[str,DataStatusRecord];readiness_packets:Mapping[str,DataReadinessPacket];evidence_gap_packets:Mapping[str,EvidenceGapPacket];entry_censuses:Mapping[str,EntryCensus];qs004_decisions:Mapping[str,QS004DecisionContract];schema_version:str="caerus_alpha_lab_dabl_projection_v2";_as_of_recorded_at:datetime|None=None;_token:object=None
    @property
    def frozen_evaluator_permitted(self):return False
    @property
    def alpha_or_lifecycle_claim_permitted(self):return False
    @property
    def asset_current_statuses(self):
        state={asset_id:CertificationStatus.DRAFT_UNVERIFIED for asset_id in self.asset_versions}
        for status in self.statuses.values():
            if state[status.asset_version_id] in {CertificationStatus.REVOKED,CertificationStatus.SUPERSEDED}:continue
            state[status.asset_version_id]=status.current_status
        for item in self.revocations.values():state[item.asset_version_id]=CertificationStatus.REVOKED
        for item in self.supersessions.values():state[item.prior_asset_version_id]=CertificationStatus.SUPERSEDED
        return MappingProxyType(state)
    @property
    def certification_current_statuses(self):
        precedence={CertificationStatus.DRAFT_UNVERIFIED:0,CertificationStatus.BLOCKED:1,CertificationStatus.REVOKED:2,CertificationStatus.SUPERSEDED:3}
        asset_statuses=self.asset_current_statuses
        state={}
        for certification_id,certification in self.certifications.items():
            asset_status=asset_statuses[certification.asset_version_id]
            state[certification_id]=asset_status if precedence[asset_status]>precedence[certification.status] else certification.status
        return MappingProxyType(state)
    @property
    def blocker_current_statuses(self):
        state={item.blocker_id:item.status for item in self.blockers.values()}
        for transition in self.blocker_transitions.values():state[transition.blocker_id]=transition.to_status
        return MappingProxyType(state)
    @property
    def packet_current_validity(self):
        return MappingProxyType({packet_id:reason=="CURRENT" for packet_id,reason in self.packet_current_reasons.items()})
    @property
    def packet_current_reasons(self):
        reasons={}
        current_statuses=self.certification_current_statuses
        for packet_id,packet in self.readiness_packets.items():
            cited_statuses={current_statuses[certification_id] for certification_id in packet.certification_ids}
            status=next((item for item in (CertificationStatus.SUPERSEDED,CertificationStatus.REVOKED,CertificationStatus.BLOCKED) if item in cited_statuses),None)
            if status is not None:
                reasons[packet_id]="CERTIFICATION_STATUS_{}".format(status.value)
            elif any(self.certifications[certification_id].freshness_deadline<self._as_of_recorded_at for certification_id in packet.certification_ids):
                reasons[packet_id]="CERTIFICATION_STALE"
            else:
                reasons[packet_id]="CURRENT"
        blocker_statuses=self.blocker_current_statuses
        for packet_id,packet in self.evidence_gap_packets.items():
            invalid=next((blocker_statuses[blocker_id] for blocker_id in packet.blocker_ids if blocker_statuses[blocker_id] not in {BlockerStatus.OPEN,BlockerStatus.REOPENED}),None)
            reasons[packet_id]="BLOCKER_STATUS_{}".format(invalid.value) if invalid is not None else "CURRENT"
        return MappingProxyType(reasons)
    def to_dict(self):return {"schema_version":self.schema_version,"authority_state":self.authority_state.value,"event_count":self.event_count,"event_chain_head":self.event_chain_head,"frozen_evaluator_permitted":False,"alpha_or_lifecycle_claim_permitted":False,"asset_current_statuses":{k:v.value for k,v in self.asset_current_statuses.items()},"certification_current_statuses":{k:v.value for k,v in self.certification_current_statuses.items()},"blocker_current_statuses":{k:v.value for k,v in self.blocker_current_statuses.items()},"packet_current_validity":dict(self.packet_current_validity),"packet_current_reasons":dict(self.packet_current_reasons),"source_registrations":{k:v.to_dict() for k,v in self.source_registrations.items()},"route_license_reviews":{k:v.to_dict() for k,v in self.route_license_reviews.items()},"asset_definitions":{k:v.to_dict() for k,v in self.asset_definitions.items()},"asset_versions":{k:v.to_dict() for k,v in self.asset_versions.items()},"licenses":{k:v.to_dict() for k,v in self.licenses.items()},"certifications":{k:v.to_dict() for k,v in self.certifications.items()},"blockers":{k:v.to_dict() for k,v in self.blockers.items()},"blocker_transitions":{k:v.to_dict() for k,v in self.blocker_transitions.items()},"revocations":{k:v.to_dict() for k,v in self.revocations.items()},"supersessions":{k:v.to_dict() for k,v in self.supersessions.items()},"replays":{k:v.to_dict() for k,v in self.replays.items()},"replay_receipts":{k:v.to_dict() for k,v in self.replay_receipts.items()},"signed_projections":{k:v.to_dict() for k,v in self.signed_projections.items()},"signed_packets":{k:v.to_dict() for k,v in self.signed_packets.items()},"statuses":{k:v.to_dict() for k,v in self.statuses.items()},"readiness_packets":{k:v.to_dict() for k,v in self.readiness_packets.items()},"evidence_gap_packets":{k:v.to_dict() for k,v in self.evidence_gap_packets.items()},"entry_censuses":{k:v.to_dict() for k,v in self.entry_censuses.items()},"qs004_decisions":{k:v.to_dict() for k,v in self.qs004_decisions.items()}}
    def __post_init__(self):
        if self._token is not _PROJECTION_TOKEN:raise ContractValidationError("projection is factory-only")
        if self.authority_state is not AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED or self.schema_version!="caerus_alpha_lab_dabl_projection_v2":raise ContractValidationError("projection authority/schema invalid")
        if not isinstance(self.event_count,int) or isinstance(self.event_count,bool) or self.event_count<0:raise ContractValidationError("projection count invalid")
        if self.event_count==0 and self.event_chain_head is not None:raise ContractValidationError("empty projection cannot have head")
        if self.event_count and self.event_chain_head is None:raise ContractValidationError("nonempty projection requires head")
        if (self.event_count==0)!=(self._as_of_recorded_at is None):raise ContractValidationError("projection as-of time/count mismatch")
        if self._as_of_recorded_at is not None:_utc(self._as_of_recorded_at,"projection as-of recorded at")
        if self.event_chain_head is not None:_h(self.event_chain_head,"projection head")
        for name in ("source_registrations","route_license_reviews","asset_definitions","asset_versions","licenses","certifications","blockers","blocker_transitions","revocations","supersessions","replays","replay_receipts","signed_projections","signed_packets","statuses","readiness_packets","evidence_gap_packets","entry_censuses","qs004_decisions"):
            value=getattr(self,name)
            if not isinstance(value,Mapping):raise ContractValidationError("projection map invalid")
            object.__setattr__(self,name,MappingProxyType(dict(value)))

def _same(a,b):return a.to_dict()==b.to_dict()
def _semantic_times(value):
    """Yield every encoded semantic timestamp for the central recorded-at bound."""
    if isinstance(value,Mapping):
        for key,item in value.items():
            if key.endswith("_at") and item is not None:
                yield parse_utc(item,key)
            yield from _semantic_times(item)
    elif isinstance(value,(tuple,list)):
        for item in value: yield from _semantic_times(item)

def project_event_plan(records:Sequence[DABLEvent])->DABLProjection:
    registrations={};reviews={};defs={};vers={};licenses={};certs={};blocks={};transitions={};blocker_states={};blocker_transitioned_at={};revs={};sups={};replays={};replay_requested_at={};replay_receipts={};signed_projections={};signed_packets={};statuses={};status_observed_at={};ready={};gaps={};censuses={};qs={};prior=None;last_time=None;last_recorded_at=None;ids=set()
    for pos,event in enumerate(records,1):
        # Reconstruct each record through strict JSON and contract decoding.
        event=DABLEvent.from_dict(event.to_dict())
        if event.previous_event_hash!=prior or event.event_id in ids:raise EventStoreIntegrityError("DABL chain/id duplicate at {}".format(pos))
        if last_time is not None and event.occurred_at<last_time:raise EventStoreIntegrityError("DABL chronology regressed")
        if pos>1 and event.recorded_at<records[pos-2].recorded_at:raise EventStoreIntegrityError("DABL recorded_at regressed")
        if any(when>event.recorded_at for when in _semantic_times(thaw(event.payload))):raise EventStoreIntegrityError("payload timestamp exceeds event recorded_at")
        payload=_EVENTS[event.event_type].from_dict(thaw(event.payload))
        if canonical_hash(payload.to_dict())!=event.payload_hash:raise EventStoreIntegrityError("payload does not canonicalize to event hash")
        if isinstance(payload,SourceRegistration):
            if payload.source_registration_id in registrations or payload.registered_at>event.occurred_at:raise EventStoreIntegrityError("source registration invalid")
            registrations[payload.source_registration_id]=payload
        elif isinstance(payload,SourceRouteLicenseReview):
            route=next((route for definition in defs.values() for route in definition.source_routes if route.route_id==payload.route_id),None)
            if payload.license_review_id in reviews or payload.license_id not in licenses or route is None or payload.reviewed_at>event.occurred_at or licenses[payload.license_id].terms_sha256!=payload.terms_sha256 or (payload.source_registration_id,payload.source_provider,payload.dataset,payload.exact_scope,payload.route_sha256)!=(route.source_registration_id,route.source_provider,route.dataset,route.exact_scope,canonical_hash(route.to_dict())):raise EventStoreIntegrityError("route license review invalid")
            reviews[payload.license_review_id]=payload
        elif isinstance(payload,DataAssetDefinition):
            if payload.asset_definition_id in defs:raise EventStoreIntegrityError("duplicate asset definition")
            registration=registrations.get(payload.source_registration_id)
            if registration is None or (registration.source_provider,registration.dataset,registration.owner)!=(payload.source_provider,payload.dataset,payload.owner):raise EventStoreIntegrityError("definition requires exact registered source identity")
            if any((route.source_registration_id,route.source_provider,route.dataset,route.owner)!=(payload.source_registration_id,payload.source_provider,payload.dataset,payload.owner) for route in payload.source_routes):raise EventStoreIntegrityError("definition route/source identity mismatch")
            if {route.route_id for definition in defs.values() for route in definition.source_routes}.intersection(route.route_id for route in payload.source_routes):raise EventStoreIntegrityError("route IDs must be globally unique")
            defs[payload.asset_definition_id]=payload
        elif isinstance(payload,DataAssetVersion):
            d=defs.get(payload.asset_definition_id)
            if d is None or payload.asset_version_id in vers or payload.license_terms.license_id not in licenses or payload.source_route_id not in {route.route_id for route in d.source_routes}:raise EventStoreIntegrityError("version dependency missing")
            if not _same(payload.license_terms,licenses[payload.license_terms.license_id]):raise EventStoreIntegrityError("embedded license differs from recorded decision")
            route=next(route for route in d.source_routes if route.route_id==payload.source_route_id)
            registration=registrations.get(payload.source_registration_id)
            if payload.source_registration_id!=d.source_registration_id or registration is None or (registration.source_provider,registration.dataset,registration.owner)!=(d.source_provider,d.dataset,d.owner) or (route.source_registration_id,route.source_provider,route.dataset,route.owner)!=(d.source_registration_id,d.source_provider,d.dataset,d.owner):raise EventStoreIntegrityError("version/source registration identity mismatch")
            if (payload.license_terms.provider,payload.license_terms.dataset)!=(d.source_provider,d.dataset):raise EventStoreIntegrityError("version license provider/dataset mismatch")
            if not any(review.route_id==route.route_id and review.license_id==payload.license_terms.license_id and review.terms_sha256==payload.license_terms.terms_sha256 and review.reviewed_at<=event.occurred_at for review in reviews.values()):raise EventStoreIntegrityError("version requires route license review")
            if payload.dependent_lane_ids!=d.dependent_lane_ids:raise EventStoreIntegrityError("version lane binding differs from definition")
            vers[payload.asset_version_id]=payload
        elif isinstance(payload,LicenseTerms):
            if payload.license_id in licenses:raise EventStoreIntegrityError("duplicate license")
            licenses[payload.license_id]=payload
        elif isinstance(payload,DataCertification):
            v=vers.get(payload.asset_version_id)
            if v is None or payload.certification_id in certs or payload.ledger_reference!=defs[v.asset_definition_id].ledger_reference:raise EventStoreIntegrityError("certification binding invalid")
            if payload.certifier_id in {payload.independent_replay.independent_reviewer_id,defs[v.asset_definition_id].owner}:raise EventStoreIntegrityError("certifier must be distinct from reviewer and producer")
            requirement=replays.get(payload.independent_replay.replay_id)
            if requirement is None or not _same(requirement,payload.independent_replay):raise EventStoreIntegrityError("certification requires projected replay requirement")
            matching_receipts=[receipt for receipt in replay_receipts.values() if receipt.replay_id==requirement.replay_id and receipt.asset_version_id==v.asset_version_id and receipt.reviewer_identity_id==requirement.independent_reviewer_id]
            cited_receipts=[receipt for receipt in matching_receipts if canonical_hash(receipt.to_dict())==payload.independent_replay_receipt_sha256] if payload.independent_replay_receipt_sha256 is not None else []
            if payload.independent_replay_receipt_sha256 is not None and len(cited_receipts)!=1:raise EventStoreIntegrityError("certification replay receipt hash invalid")
            causal_times=[v.availability_at,v.retrieved_at,v.ingested_at,v.model_available_at,requirement.requested_at]
            causal_times.extend(parse_utc(fact["observed_at"],"fact observed_at") for fact in v.facts.values())
            if v.license_terms.accepted_at is not None:causal_times.append(v.license_terms.accepted_at)
            if cited_receipts:causal_times.append(cited_receipts[0].completed_at)
            if any(payload.certified_at<when for when in causal_times):raise EventStoreIntegrityError("certification causal lower bound invalid")
            bound_receipts=[receipt for receipt in matching_receipts if receipt.completed_at<=payload.certified_at]
            if payload.tier is DataTier.A and (len(bound_receipts)!=1 or payload.independent_replay_receipt_sha256!=canonical_hash(bound_receipts[0].to_dict()) or payload.certifier_id==bound_receipts[0].producer_identity_id or not v.license_terms.ai_use_permitted):raise EventStoreIntegrityError("Tier A requires exact completed independent replay receipt and permitted license")
            if payload.tier is DataTier.A and any(fact["status"]!=FactStatus.EVIDENCED.value for fact in v.facts.values()):raise EventStoreIntegrityError("Tier A certification requires all facts evidenced")
            if all(f["status"]==FactStatus.NOT_APPLICABLE.value for f in v.facts.values()):raise EventStoreIntegrityError("all-N/A asset cannot certify in v2 prep")
            certs[payload.certification_id]=payload
        elif isinstance(payload,DataBlocker):
            d=defs.get(payload.asset_definition_id)
            if d is None or payload.blocker_id in blocks or payload.ledger_reference!=d.ledger_reference or set(payload.route_ids)-{r.route_id for r in d.source_routes}:raise EventStoreIntegrityError("blocker binding invalid")
            if payload.status is not BlockerStatus.OPEN:raise EventStoreIntegrityError("blocker creation must begin OPEN")
            blocks[payload.blocker_id]=payload
            blocker_states[payload.blocker_id]=BlockerStatus.OPEN
            blocker_transitioned_at[payload.blocker_id]=payload.created_at
        elif isinstance(payload,BlockerTransition):
            if payload.transition_id in transitions or payload.blocker_id not in blocks or payload.from_status is not blocker_states[payload.blocker_id] or payload.transitioned_at>event.occurred_at or payload.transitioned_at<blocker_transitioned_at[payload.blocker_id]:raise EventStoreIntegrityError("blocker transition invalid")
            transitions[payload.transition_id]=payload;blocker_states[payload.blocker_id]=payload.to_status;blocker_transitioned_at[payload.blocker_id]=payload.transitioned_at
        elif isinstance(payload,RevocationRecord):
            if payload.asset_version_id not in vers or payload.revocation_id in revs or payload.revoked_at>event.occurred_at or payload.revoked_at<vers[payload.asset_version_id].model_available_at:raise EventStoreIntegrityError("revocation binding invalid")
            revs[payload.revocation_id]=payload
        elif isinstance(payload,SupersessionRecord):
            if payload.prior_asset_version_id not in vers or payload.replacement_asset_version_id not in vers or payload.supersession_id in sups or payload.recorded_at>event.occurred_at or payload.recorded_at<vers[payload.prior_asset_version_id].model_available_at or payload.recorded_at<vers[payload.replacement_asset_version_id].model_available_at or vers[payload.prior_asset_version_id].asset_definition_id!=vers[payload.replacement_asset_version_id].asset_definition_id:raise EventStoreIntegrityError("supersession binding invalid")
            sups[payload.supersession_id]=payload
        elif isinstance(payload,IndependentReplayRequirement):
            if payload.asset_version_id not in vers or payload.replay_id in replays or payload.requested_at>event.occurred_at or payload.requested_at<vers[payload.asset_version_id].model_available_at:raise EventStoreIntegrityError("replay binding invalid")
            replays[payload.replay_id]=payload;replay_requested_at[payload.replay_id]=payload.requested_at
        elif isinstance(payload,IndependentReplayReceipt):
            if payload.replay_receipt_id in replay_receipts or payload.replay_id not in replays or payload.asset_version_id not in vers or replays[payload.replay_id].asset_version_id!=payload.asset_version_id or payload.completed_at>event.occurred_at or payload.completed_at<vers[payload.asset_version_id].model_available_at or payload.completed_at<replay_requested_at[payload.replay_id] or payload.dabl_head_hash!=prior:raise EventStoreIntegrityError("replay receipt binding invalid")
            replay_receipts[payload.replay_receipt_id]=payload
        elif isinstance(payload,SignedProjectionEvent):
            if payload.projection_id in signed_projections or payload.signed_at>event.recorded_at or payload.signed_at<payload.binding_receipt.exported_at or payload.projection_sha256!=payload.binding_receipt.projection_sha256:raise EventStoreIntegrityError("signed projection invalid")
            signed_projections[payload.projection_id]=payload
        elif isinstance(payload,SignedPacketEvent):
            if payload.packet_signature_id in signed_packets or payload.signed_at>event.recorded_at or payload.signed_at<payload.binding_receipt.exported_at or payload.packet_id not in set(ready)|set(gaps) or payload.external_verification_state is not ExternalVerificationState.EXTERNAL_VERIFIER_REQUIRED:raise EventStoreIntegrityError("signed packet invalid")
            packets={**ready,**gaps}
            if payload.signed_at<packets[payload.packet_id].created_at or canonical_hash(packets[payload.packet_id].to_dict())!=payload.packet_sha256 or payload.binding_receipt!=packets[payload.packet_id].ledger_reference.binding_receipt:raise EventStoreIntegrityError("signed packet hash/binding invalid")
            signed_packets[payload.packet_signature_id]=payload
        elif isinstance(payload,DataStatusRecord):
            prior_status=next((item.current_status for item in reversed(tuple(statuses.values())) if item.asset_version_id==payload.asset_version_id),CertificationStatus.DRAFT_UNVERIFIED)
            terminal_revocation=next((item for item in revs.values() if item.asset_version_id==payload.asset_version_id),None)
            terminal_supersession=next((item for item in sups.values() if item.prior_asset_version_id==payload.asset_version_id),None)
            terminal_mismatch=(terminal_supersession is not None and (payload.current_status is not CertificationStatus.SUPERSEDED or payload.supersession_id!=terminal_supersession.supersession_id)) or (terminal_supersession is None and terminal_revocation is not None and (payload.current_status is not CertificationStatus.REVOKED or payload.revocation_id!=terminal_revocation.revocation_id))
            referenced_revocation=revs.get(payload.revocation_id) if payload.revocation_id else None
            referenced_supersession=sups.get(payload.supersession_id) if payload.supersession_id else None
            if payload.asset_version_id not in vers or payload.status_id in statuses or prior_status in {CertificationStatus.REVOKED,CertificationStatus.SUPERSEDED} or terminal_mismatch or payload.observed_at>event.occurred_at or payload.observed_at<vers[payload.asset_version_id].model_available_at or (payload.asset_version_id in status_observed_at and payload.observed_at<status_observed_at[payload.asset_version_id]) or (payload.revocation_id and (referenced_revocation is None or referenced_revocation.asset_version_id!=payload.asset_version_id or payload.observed_at<referenced_revocation.revoked_at)) or (payload.supersession_id and (referenced_supersession is None or referenced_supersession.prior_asset_version_id!=payload.asset_version_id or payload.observed_at<referenced_supersession.recorded_at)):raise EventStoreIntegrityError("status binding invalid")
            statuses[payload.status_id]=payload;status_observed_at[payload.asset_version_id]=payload.observed_at
        elif isinstance(payload,DataReadinessPacket):
            if payload.packet_id in ready or any(x not in defs for x in payload.asset_definition_ids) or any(x not in certs for x in payload.certification_ids):raise EventStoreIntegrityError("readiness dependency invalid")
            if any(certs[x].ledger_reference!=payload.ledger_reference for x in payload.certification_ids):raise EventStoreIntegrityError("readiness ledger binding invalid")
            if set(payload.asset_definition_ids)!={vers[certs[x].asset_version_id].asset_definition_id for x in payload.certification_ids}:raise EventStoreIntegrityError("readiness assets must derive exactly from cited certifications")
            for certification_id in payload.certification_ids:
                certification=certs[certification_id];asset=vers[certification.asset_version_id];requirement=replays[certification.independent_replay.replay_id]
                causal_times=[certification.certified_at,asset.availability_at,asset.retrieved_at,asset.ingested_at,asset.model_available_at,requirement.requested_at]
                causal_times.extend(parse_utc(fact["observed_at"],"fact observed_at") for fact in asset.facts.values())
                if asset.license_terms.accepted_at is not None:causal_times.append(asset.license_terms.accepted_at)
                if certification.independent_replay_receipt_sha256 is not None:
                    cited=[receipt for receipt in replay_receipts.values() if canonical_hash(receipt.to_dict())==certification.independent_replay_receipt_sha256]
                    if len(cited)!=1:raise EventStoreIntegrityError("readiness replay receipt dependency invalid")
                    causal_times.append(cited[0].completed_at)
                if any(payload.created_at<when for when in causal_times):raise EventStoreIntegrityError("readiness causal lower bound invalid")
            if any(certs[x].tier is not payload.tier or certs[x].status is not CertificationStatus.DRAFT_UNVERIFIED or certs[x].freshness_deadline<event.recorded_at for x in payload.certification_ids):raise EventStoreIntegrityError("readiness current-tier/replay/freshness invalid")
            if any(not vers[certs[x].asset_version_id].license_terms.ai_use_permitted for x in payload.certification_ids):raise EventStoreIntegrityError("readiness license no longer permits required use")
            current_assets={certs[x].asset_version_id for x in payload.certification_ids}
            if any(rev.asset_version_id in current_assets for rev in revs.values()) or any(sup.prior_asset_version_id in current_assets for sup in sups.values()) or any(status.asset_version_id in current_assets and status.current_status in {CertificationStatus.REVOKED,CertificationStatus.SUPERSEDED} for status in statuses.values()):raise EventStoreIntegrityError("readiness asset is revoked/superseded")
            if payload.tier is DataTier.A:
                for certification_id in payload.certification_ids:
                    certification=certs[certification_id]; asset=vers[certification.asset_version_id]
                    if any(fact["status"]!=FactStatus.EVIDENCED.value for fact in asset.facts.values()):raise EventStoreIntegrityError("Tier A requires all facts evidenced")
                    if not any(receipt.replay_id==certification.independent_replay.replay_id and receipt.asset_version_id==asset.asset_version_id and receipt.completed_at<=certification.certified_at and receipt.reviewer_identity_id==certification.independent_replay.independent_reviewer_id and canonical_hash(receipt.to_dict())==certification.independent_replay_receipt_sha256 for receipt in replay_receipts.values()):raise EventStoreIntegrityError("Tier A requires completed projected independent replay receipt")
                    if any(status.asset_version_id==asset.asset_version_id and status.current_status in {CertificationStatus.REVOKED,CertificationStatus.SUPERSEDED} for status in statuses.values()):raise EventStoreIntegrityError("readiness asset is no longer current")
            ready[payload.packet_id]=payload
        elif isinstance(payload,EvidenceGapPacket):
            if payload.packet_id in gaps or any(x not in blocks for x in payload.blocker_ids) or any(blocks[x].ledger_reference!=payload.ledger_reference for x in payload.blocker_ids) or any(blocker_states[x] not in {BlockerStatus.OPEN,BlockerStatus.REOPENED} for x in payload.blocker_ids):raise EventStoreIntegrityError("gap dependency invalid")
            if any(payload.created_at<blocker_transitioned_at[blocker_id] for blocker_id in payload.blocker_ids):raise EventStoreIntegrityError("gap causal lower bound invalid")
            expected_route_ids={route_id for blocker_id in payload.blocker_ids for route_id in blocks[blocker_id].route_ids}
            registered_routes={route.route_id:route for blocker_id in payload.blocker_ids for route in defs[blocks[blocker_id].asset_definition_id].source_routes if route.route_id in blocks[blocker_id].route_ids}
            packet_route_ids={route.route_id for route in payload.source_routes}
            if packet_route_ids!=expected_route_ids or len(packet_route_ids)!=len(payload.source_routes) or set(registered_routes)!=expected_route_ids or any(not _same(route,registered_routes[route.route_id]) for route in payload.source_routes):raise EventStoreIntegrityError("gap route is not exact blocker/definition route")
            gaps[payload.packet_id]=payload
        elif isinstance(payload,EntryCensus):
            if payload.census_id in censuses:raise EventStoreIntegrityError("duplicate census")
            censuses[payload.census_id]=payload
        elif isinstance(payload,QS004DecisionContract):
            if payload.qs004_id in qs or payload.entry_census.census_id not in censuses or not _same(payload.entry_census,censuses[payload.entry_census.census_id]):raise EventStoreIntegrityError("QS004 dependency invalid")
            census=payload.entry_census
            census_assets={asset_id for assets in census.lane_to_asset_ids.values() for asset_id in assets}
            if census_assets!=set(defs):raise EventStoreIntegrityError("QS004 census assets must equal materialized definitions")
            if any(set(definition.dependent_lane_ids)!={lane_id for lane_id,assets in census.lane_to_asset_ids.items() if definition.asset_definition_id in assets} for definition in defs.values()):raise EventStoreIntegrityError("QS004 census lanes must equal definition dependent lanes")
            materialized_routes={route.route_id for definition in defs.values() for route in definition.source_routes}
            if any(set(route_ids)-materialized_routes for route_ids in census.candidate_viable_untried_route_ids.values()):raise EventStoreIntegrityError("QS004 viable routes must be materialized")
            qs[payload.qs004_id]=payload
        prior=event.event_hash;last_time=event.occurred_at;last_recorded_at=event.recorded_at;ids.add(event.event_id)
    mp=lambda d:MappingProxyType(dict(d))
    return DABLProjection(AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED,len(records),prior,mp(registrations),mp(reviews),mp(defs),mp(vers),mp(licenses),mp(certs),mp(blocks),mp(transitions),mp(revs),mp(sups),mp(replays),mp(replay_receipts),mp(signed_projections),mp(signed_packets),mp(statuses),mp(ready),mp(gaps),mp(censuses),mp(qs),_as_of_recorded_at=last_recorded_at,_token=_PROJECTION_TOKEN)

def plan_append(records:Sequence[DABLEvent],*,expected_previous_head:str|None,event_id:str,event_type:str,occurred_at:datetime,payload:Mapping[str,Any],recorded_at:datetime|None=None)->Tuple[DABLEvent,...]:
    """Pure CAS append plan; validates full prospective replay before any I/O."""
    current=project_event_plan(records)
    _id(event_id,"event_id");_utc(occurred_at,"occurred_at")
    recorded_at=occurred_at if recorded_at is None else recorded_at;_utc(recorded_at,"recorded_at")
    if recorded_at<occurred_at:raise ContractValidationError("recorded_at precedes occurred_at")
    if event_type not in _EVENTS:raise ContractValidationError("event type unsupported")
    typed=_EVENTS[event_type].from_dict(payload);canonical=typed.to_dict();unsigned={"schema_version":"caerus_alpha_lab_dabl_event_v3","event_id":event_id,"event_type":event_type,"occurred_at":format_datetime(occurred_at),"recorded_at":format_datetime(recorded_at),"payload":canonical,"payload_hash":canonical_hash(canonical),"previous_event_hash":expected_previous_head}
    existing=next((item for item in records if item.event_id==event_id),None)
    if existing is not None:
        if existing.event_type==event_type and existing.occurred_at==occurred_at and existing.recorded_at==recorded_at and existing.payload_hash==canonical_hash(canonical) and existing.previous_event_hash==expected_previous_head: return tuple(records)
        raise EventStoreIntegrityError("event ID already exists with different content")
    if current.event_chain_head!=expected_previous_head:raise EventStoreIntegrityError("expected previous head CAS mismatch")
    if records and occurred_at<records[-1].occurred_at:raise ContractValidationError("occurred_at must be monotonic")
    event=DABLEvent(event_id,event_type,occurred_at,recorded_at,canonical,unsigned["payload_hash"],expected_previous_head,canonical_hash(unsigned))
    proposed=tuple(records)+(event,);project_event_plan(proposed)
    return proposed
