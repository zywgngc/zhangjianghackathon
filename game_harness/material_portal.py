"""Validated HTTP bridge to the team's proposed MaterialPortalPort, never a mock fallback."""
import hashlib
import json
import uuid

import httpx
from jsonschema import Draft202012Validator, FormatChecker, ValidationError


class PortalContractError(ValueError):
    pass


class MaterialPortalClient:
    def __init__(self, settings, transport=None):
        self.settings, self.transport = settings, transport
        schema = json.loads(settings.portal_schema.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        self.validators = {name: Draft202012Validator({"$schema": schema["$schema"], "$defs": schema["$defs"],
            "$ref": "#/$defs/" + name}, format_checker=FormatChecker())
            for name in ["MaterialSearchRequest", "MaterialSearchResult"]}

    def validate_request(self, request):
        try:
            self.validators["MaterialSearchRequest"].validate(request)
        except ValidationError as exc:
            raise PortalContractError("invalid request at " + "/".join(map(str, exc.absolute_path))) from None

    def validate_result(self, request, result):
        try:
            self.validators["MaterialSearchResult"].validate(result)
        except ValidationError as exc:
            raise PortalContractError("invalid result at " + "/".join(map(str, exc.absolute_path))) from None
        if result["requestId"] != request["requestId"] or result["contractVersion"] != request["contractVersion"]:
            raise PortalContractError("request/version mismatch")
        if result["discussion"] != {k: request["discussion"][k] for k in ("runId", "threadId", "round")}:
            raise PortalContractError("discussion correlation mismatch")
        if result["status"] != "failed":
            candidates = result["candidates"]
            if len(candidates) > request["limit"]:
                raise PortalContractError("candidate limit exceeded")
            if len({c["candidateId"] for c in candidates}) != len(candidates):
                raise PortalContractError("duplicate candidate IDs")
            if any(c["kind"] not in request["materialKinds"] for c in candidates):
                raise PortalContractError("unrequested material kind")
        if result["status"] == "needs_clarification":
            questions = result["questions"]
            if len({q["questionId"] for q in questions}) != len(questions):
                raise PortalContractError("duplicate clarification question IDs")
            for question in questions:
                options = [option["optionId"] for option in question["options"]]
                if len(set(options)) != len(options):
                    raise PortalContractError("duplicate clarification options")
                if not question["required"] and question["defaultOptionId"] not in options:
                    raise PortalContractError("clarification default is not an option")

    def request_for(self, query, limit, run_id):
        request = {"contractVersion": "0.2", "requestId": "pending", "projectRevision": 0,
            "discussion": {"runId": run_id, "threadId": "archetypes", "round": 0,
                           "inReplyToRequestId": None, "followUp": None},
            "purpose": "archetype", "description": query, "images": [],
            "constraints": {"engine": "html", "engineVersion": None, "platform": "web", "language": "any",
                            "inputModes": ["keyboard", "pointer", "touch"]},
            "materialKinds": ["game_repository", "source_code", "algorithm_documentation", "reference_media"],
            "limit": min(10, max(1, limit))}
        request["requestId"] = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self.validate_request(request)
        return request

    async def search(self, query, limit=4, run_id=None):
        # Standalone knowledge lookups have their own correlation ID; workflow
        # retrieval supplies its actual durable run ID. This bridge dispatches
        # initial searches only and never invents follow-up answers or counters.
        request = self.request_for(query, limit, run_id or "lookup-" + uuid.uuid4().hex)
        headers = {"Idempotency-Key": request["requestId"]}
        if self.settings.portal_token:
            headers["Authorization"] = "Bearer " + self.settings.portal_token
        trace = {"request": request}
        try:
            # No blind retries of potentially paid searches without provider job reconciliation.
            async with httpx.AsyncClient(timeout=12, trust_env=False, transport=self.transport) as client:
                response = await client.post(self.settings.portal_url, json=request, headers=headers)
                response.raise_for_status()
                if len(response.content) > 1000000:
                    raise PortalContractError("response too large")
                result = response.json()
            self.validate_result(request, result)
            trace["result"] = result
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            return {"documents": [], "warnings": ["素材门户请求或契约校验失败：" + type(exc).__name__], "trace": trace}
        if result["status"] == "failed":
            return {"documents": [], "warnings": ["素材门户返回失败：" + result["error"]["code"]], "trace": trace}
        if result["isMock"]:
            return {"documents": [], "warnings": ["素材门户返回 mock 样例，未用作真实检索证据"], "trace": trace}
        if result["status"] == "needs_clarification":
            trace["discussion_outcome"] = "unresolved_clarification"
            return {"documents": [], "warnings": ["素材门户需要澄清；本适配器仅支持首轮检索，问题与候选已保留，未伪造答复"], "trace": trace}
        documents = []
        for candidate in result["candidates"]:
            identity = result["provider"] + json.dumps(candidate["source"], sort_keys=True)
            documents.append({"id": "portal-" + hashlib.sha256(identity.encode()).hexdigest()[:16],
                "title": candidate["title"], "content": (candidate["summary"] + "\n候选覆盖提示（待核实）：" +
                    "；".join(candidate["coverageHints"]) + "\n局限：" + "；".join(candidate["limitations"]))[:2500],
                "source": candidate["source"]["uri"], "origin": "material_portal", "verification": "unverified_candidate",
                "candidate_id": candidate["candidateId"], "provider": result["provider"],
                "revision": candidate["source"]["revision"], "license": candidate["license"]})
        return {"documents": documents, "warnings": [str(x)[:300] for x in result["limitations"]], "trace": trace}
