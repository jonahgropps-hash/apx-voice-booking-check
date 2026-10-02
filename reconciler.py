"""Read-only acceptance checker for normalized synthetic/exported evidence; no APIs."""
import argparse
import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

FIELDS = ("locationId", "calendarId", "contactId")
STATUSES = {"confirmed", "cancelled", "invalid"}


def reconcile(bundle):
    findings = []
    metrics = {"booking_attempts": 0, "new_active_target_records": None,
               "exact_eligible_records": None}

    def add(code, severity="UNKNOWN"):
        item = {"code": code, "severity": severity}
        if item not in findings:
            findings.append(item)

    def finish():
        verdict = "FAIL" if any(x["severity"] == "FAIL" for x in findings) else (
            "UNKNOWN" if any(x["severity"] == "UNKNOWN" for x in findings) else "PASS")
        raw_id = contract.get("case_id", "") if isinstance(contract, dict) else ""
        case_id = raw_id if isinstance(raw_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", raw_id) else "redacted"
        return {"report_version": "1.0", "case_id": case_id, "verdict": verdict,
                "findings": findings, "metrics": metrics,
                "evidence_kind": "normalized bundle; adapter and voice performance unverified"}

    def timestamp(value, prefix):
        try:
            if not isinstance(value, str):
                raise ValueError()
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if result.tzinfo is None or result.utcoffset() is None:
                add("NAIVE_DATETIME")
                return None
            return result
        except (ValueError, TypeError, OverflowError):
            add(prefix + "_INVALID_DATETIME")
            return None

    def epoch(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            add("CALL_TIMING_MISSING")
            return None
        try:
            return datetime.fromtimestamp(value / 1000, timezone.utc)
        except (ValueError, OverflowError, OSError):
            add("CALL_TIMING_INVALID")
            return None

    contract = bundle.get("contract", {}) if isinstance(bundle, dict) else {}
    if not isinstance(bundle, dict) or bundle.get("schema_version") != "1.0":
        add("BUNDLE_SCHEMA_UNKNOWN")
        return finish()
    if not isinstance(contract, dict) or not isinstance(contract.get("operation"), str) or contract.get("operation") not in {"book", "reject"}:
        add("CONTRACT_INVALID")
        return finish()
    expected = contract.get("expected")
    if not isinstance(contract.get("case_id"), str) or not contract["case_id"]:
        add("CONTRACT_CASE_ID_MISSING")
    if not isinstance(expected, dict) or any(not isinstance(expected.get(k), str) or not expected[k] for k in FIELDS):
        add("CONTRACT_INVALID")
        return finish()
    start = timestamp(expected.get("startTime"), "CONTRACT")
    end = timestamp(expected.get("endTime"), "CONTRACT")
    if start is None or end is None:
        return finish()
    if end <= start or type(contract.get("allow_existing", False)) is not bool:
        add("CONTRACT_INVALID")
        return finish()
    booking_tool_name = contract.get("booking_tool_name", "book_appointment")
    if not isinstance(booking_tool_name, str) or not booking_tool_name.strip():
        add("BOOKING_TOOL_NAME_INVALID")
        return finish()
    limit = contract.get("snapshot_max_age_ms", 300000)
    if type(limit) is not int or limit < 0:
        add("CONTRACT_INVALID")
        return finish()

    def target(record):
        return all(record.get(k) == expected[k] for k in FIELDS)

    def exact(record):
        return target(record) and record["_start"] == start and record["_end"] == end

    def mismatch(record, prefix):
        if not target(record):
            add(prefix + "_TARGET_MISMATCH", "FAIL")
        if record["_start"] != start or record["_end"] != end:
            same_wall = record["_start"].replace(tzinfo=None) == start.replace(tzinfo=None)
            different_offset = record["_start"].utcoffset() != start.utcoffset()
            add(prefix + ("_TIMEZONE_MISMATCH" if same_wall and different_offset else "_TIME_MISMATCH"), "FAIL")

    def projected(record, prefix):
        if not isinstance(record, dict) or any(not isinstance(record.get(k), str) or not record[k] for k in FIELDS):
            add(prefix + "_SCHEMA_UNKNOWN")
            return None
        s = timestamp(record.get("startTime"), prefix)
        e = timestamp(record.get("endTime"), prefix)
        if s is None or e is None:
            return None
        if e <= s:
            add(prefix + "_INTERVAL_INVALID")
            return None
        return {**record, "_start": s, "_end": e}

    call = bundle.get("call")
    call_start = call_end = None
    claimed = None
    booking_results = []
    failed_attempts = 0
    if not isinstance(call, dict):
        add("MISSING_CALL")
    else:
        if not isinstance(call.get("call_id"), str) or not call["call_id"]:
            add("CALL_ID_MISSING")
        if call.get("call_status") != "ended":
            add("CALL_NOT_ENDED")
        call_start, call_end = epoch(call.get("start_timestamp")), epoch(call.get("end_timestamp"))
        if call_start and call_end and call_end <= call_start:
            add("CALL_TIMING_INVALID")
        observation = call.get("observation")
        if not isinstance(observation, dict) or observation.get("reviewed") is not True:
            add("CLAIM_NOT_REVIEWED")
        elif type(observation.get("claimed_booking")) is not bool:
            add("CLAIM_ANNOTATION_MISSING")
        else:
            claimed = observation["claimed_booking"]
        trace = call.get("transcript_with_tool_calls")
        if not isinstance(trace, list):
            add("TOOL_TRACE_MISSING")
            trace = []
        invocations, results, positions = {}, {}, {}
        for position, entry in enumerate(trace):
            if not isinstance(entry, dict):
                add("TOOL_TRACE_SCHEMA_UNKNOWN")
                continue
            role = entry.get("role")
            if not isinstance(role, str):
                add("TOOL_TRACE_SCHEMA_UNKNOWN")
                continue
            if role not in {"tool_call_invocation", "tool_call_result"}:
                continue
            tool_id = entry.get("tool_call_id")
            if not isinstance(tool_id, str) or not tool_id:
                add("TOOL_CALL_ID_MISSING")
                continue
            collection = invocations if role == "tool_call_invocation" else results
            if tool_id in collection:
                add("DUPLICATE_TOOL_CALL_ID")
            collection[tool_id] = entry
            positions[(role, tool_id)] = position
        for tool_id in results.keys() - invocations.keys():
            add("UNPAIRED_TOOL_RESULT")
        for tool_id, invocation in invocations.items():
            if tool_id not in results:
                add("MISSING_TOOL_RESULT")
            name = invocation.get("name")
            if not isinstance(name, str) or not name.strip():
                add("TOOL_NAME_INVALID")
                continue
            if tool_id in results and positions[("tool_call_result", tool_id)] <= positions[("tool_call_invocation", tool_id)]:
                add("TOOL_RESULT_PRECEDES_INVOCATION")
            if name != booking_tool_name:
                continue
            metrics["booking_attempts"] += 1
            try:
                if not isinstance(invocation.get("arguments"), str):
                    raise ValueError()
                arguments = json.loads(invocation["arguments"])
            except (ValueError, TypeError):
                add("TOOL_ARGUMENTS_INVALID_JSON")
                arguments = None
            arguments = projected(arguments, "TOOL_ARGUMENTS")
            if arguments is not None:
                mismatch(arguments, "TOOL")
            if tool_id not in results:
                continue
            envelope = results[tool_id]
            if "successful" in envelope:
                if type(envelope["successful"]) is not bool:
                    add("TOOL_RESULT_ENVELOPE_INVALID")
            try:
                raw = results[tool_id].get("content")
                if not isinstance(raw, str):
                    raise ValueError()
                result = json.loads(raw)
            except (ValueError, TypeError):
                add("TOOL_RESULT_INVALID_JSON")
                continue
            if not isinstance(result, dict) or type(result.get("success")) is not bool:
                add("TOOL_RESULT_SCHEMA_UNKNOWN")
            elif envelope.get("successful") is False and result["success"] is True:
                add("TOOL_RESULT_SUCCESS_CONTRADICTION", "FAIL")
            elif result["success"] is False:
                failed_attempts += 1
            elif not isinstance(result.get("appointmentId"), str) or not result["appointmentId"]:
                add("TOOL_RESULT_SCHEMA_UNKNOWN")
            else:
                booking_results.append(result["appointmentId"])
        if metrics["booking_attempts"] > 1:
            add("RETRY_OBSERVED", "INFO")
        if failed_attempts and contract["operation"] == "book":
            add("FAILED_ATTEMPT_RECOVERED", "INFO") if booking_results else add("TOOL_REPORTED_FAILURE", "FAIL")
        if contract["operation"] == "book" and metrics["booking_attempts"] == 0:
            add("BOOKING_TOOL_EVIDENCE_MISSING")

    def snapshot(name):
        data = bundle.get(name)
        if not isinstance(data, dict):
            add("MISSING_SNAPSHOT")
            return None
        quality_before = len([x for x in findings if x["severity"] == "UNKNOWN"])
        if data.get("complete") is not True:
            add("INCOMPLETE_BACKEND_SNAPSHOT")
        scope = data.get("scope")
        if not isinstance(scope, dict) or any(scope.get(k) != expected[k] for k in ("locationId", "calendarId")):
            add("SNAPSHOT_SCOPE_MISMATCH")
        captured = timestamp(data.get("captured_at"), "SNAPSHOT")
        if captured is not None and call_start is not None and call_end is not None:
            delta = ((call_start - captured) if name == "before_snapshot" else (captured - call_end)).total_seconds() * 1000
            if delta < 0:
                add("SNAPSHOT_WRONG_TIME_ORDER")
            elif delta > limit:
                add("STALE_SNAPSHOT")
        elif call_start is None or call_end is None:
            add("SNAPSHOT_CALL_TIMING_UNVERIFIED")
        raw_records = data.get("appointments")
        if not isinstance(raw_records, list):
            add("SNAPSHOT_SCHEMA_UNKNOWN")
            return None
        records = {}
        for raw_record in raw_records:
            record = projected(raw_record, "APPOINTMENT")
            if record is None:
                continue
            record_id = record.get("id")
            if not isinstance(record_id, str) or not record_id:
                add("APPOINTMENT_ID_MISSING")
                continue
            if record_id in records:
                add("DUPLICATE_SNAPSHOT_RECORD_ID")
            if not isinstance(record.get("status"), str) or record.get("status") not in STATUSES:
                add("APPOINTMENT_STATUS_UNKNOWN")
            if any(record.get(k) != expected[k] for k in ("locationId", "calendarId")):
                add("SNAPSHOT_RECORD_OUTSIDE_SCOPE")
            if name == "after_snapshot" and record_id in booking_results:
                mismatch(record, "BOOKING")
                if contract["operation"] == "book" and isinstance(record.get("status"), str) and record.get("status") in {"cancelled", "invalid"}:
                    add("BOOKING_NOT_ACTIVE", "FAIL")
            records[record_id] = record
        quality_after = len([x for x in findings if x["severity"] == "UNKNOWN"])
        return records if quality_before == quality_after else None

    before = snapshot("before_snapshot")
    after = snapshot("after_snapshot")
    # Do not infer absence or duplicate counts from incomplete/ambiguous projections.
    if before is not None and after is not None:
        def became_active_target(r):
            old = before.get(r["id"])
            return old is None or old["status"] != "confirmed" or not target(old) or old["_start"] != r["_start"] or old["_end"] != r["_end"]

        active_target = [r for r in after.values() if target(r) and r["status"] == "confirmed" and became_active_target(r)]
        all_exact_active = [r for r in after.values() if exact(r) and r["status"] == "confirmed"]
        eligible = [r for r in after.values() if exact(r) and r["status"] == "confirmed"
                    and (r["id"] not in before or contract.get("allow_existing", False))]
        metrics["new_active_target_records"] = len(active_target)
        metrics["exact_eligible_records"] = len(eligible)
        if len(active_target) > 1 or len(eligible) > 1 or (contract["operation"] == "book" and len(all_exact_active) > 1):
            add("DUPLICATE_ACTIVE_TARGET", "FAIL")
        if contract["operation"] == "reject":
            if active_target:
                add("INELIGIBLE_CASE_BOOKED", "FAIL")
            if claimed is True:
                add("INELIGIBLE_CASE_CLAIMED_BOOKING", "FAIL")
        else:
            if claimed is False:
                add("EXPECTED_BOOKING_NOT_CLAIMED", "FAIL")
            if not eligible:
                if any(exact(r) and r["status"] == "confirmed" for r in before.values()):
                    add("PREEXISTING_BOOKING_NOT_NEW", "FAIL")
                else:
                    add("CLAIM_WITHOUT_DURABLE_BOOKING" if claimed is True else "EXPECTED_BOOKING_MISSING", "FAIL")
            for record_id in booking_results:
                record = after.get(record_id)
                if record is None:
                    add("TOOL_RESULT_RECORD_NOT_FOUND", "FAIL")
                else:
                    mismatch(record, "BOOKING")
                    if record["status"] != "confirmed":
                        add("BOOKING_NOT_ACTIVE", "FAIL")
            if not booking_results:
                add("SUCCESSFUL_TOOL_RESULT_MISSING")
            elif eligible and any(record_id != eligible[0]["id"] for record_id in booking_results):
                add("TOOL_RESULT_RECORD_MISMATCH", "FAIL")
    return finish()


def html_report(report):
    safe = html.escape(json.dumps(report, indent=2))
    return '<!doctype html><html lang="en"><meta charset="utf-8"><title>Booking evidence reconciliation</title><style>body{font:16px system-ui;margin:2rem;max-width:900px}pre{white-space:pre-wrap;background:#f3f4f6;padding:1rem}</style><h1>Booking evidence reconciliation</h1><p>Read-only normalized evidence check. Not a voice-agent performance result or live integration certification.</p><pre>' + safe + '</pre></html>'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--html-out", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.bundle.stat().st_size > 10_000_000:
            raise ValueError()
        bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
        report = reconcile(bundle)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        report = {"report_version": "1.0", "case_id": "redacted", "verdict": "UNKNOWN",
                  "findings": [{"code": "INPUT_UNREADABLE_OR_INVALID", "severity": "UNKNOWN"}]}
    encoded = json.dumps(report, indent=2)
    if args.json_out:
        args.json_out.write_text(encoded + "\n", encoding="utf-8")
    if args.html_out:
        args.html_out.write_text(html_report(report), encoding="utf-8")
    print(encoded)
    return {"PASS": 0, "FAIL": 1, "UNKNOWN": 2}[report["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
