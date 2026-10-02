"""Synthetic fixtures. No calls, real tenants, customer data, or services."""
import copy
import json
from datetime import datetime


def ms(value):
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def baseline():
    expected = {"locationId": "loc_synthetic", "calendarId": "cal_synthetic",
                "contactId": "contact_synthetic", "startTime": "2026-10-03T09:00:00-04:00",
                "endTime": "2026-10-03T10:00:00-04:00"}
    appointment = {**expected, "id": "apt_synthetic_1", "status": "confirmed"}
    scope = {k: expected[k] for k in ("locationId", "calendarId")}
    return {"schema_version": "1.0",
            "contract": {"case_id": "synthetic_success", "operation": "book",
                         "allow_existing": False, "expected": expected},
            "call": {"call_id": "call_synthetic", "call_status": "ended",
                     "start_timestamp": ms("2026-10-01T14:00:00-04:00"),
                     "end_timestamp": ms("2026-10-01T14:03:00-04:00"),
                     "observation": {"reviewed": True, "claimed_booking": True},
                     "transcript_with_tool_calls": [
                         {"role": "tool_call_invocation", "tool_call_id": "tool_1",
                          "name": "book_appointment", "arguments": json.dumps(expected)},
                         {"role": "tool_call_result", "tool_call_id": "tool_1",
                          "content": json.dumps({"success": True, "appointmentId": "apt_synthetic_1"})}]},
            "before_snapshot": {"captured_at": "2026-10-01T13:59:00-04:00", "scope": scope,
                                "complete": True, "appointments": []},
            "after_snapshot": {"captured_at": "2026-10-01T14:03:05-04:00", "scope": scope,
                               "complete": True, "appointments": [appointment]}}


def cases():
    result = []

    def add(name, mutate, verdict, code=None):
        bundle = baseline()
        bundle["contract"]["case_id"] = name
        mutate(bundle)
        result.append({"name": name, "expected_verdict": verdict,
                       "expected_reason": code, "bundle": bundle})

    add("baseline_success", lambda b: None, "PASS")
    add("false_booking_claim", lambda b: b["after_snapshot"].update(appointments=[]), "FAIL", "CLAIM_WITHOUT_DURABLE_BOOKING")
    for field in ("locationId", "calendarId", "contactId"):
        add("wrong_" + field, lambda b, f=field: b["after_snapshot"]["appointments"][0].update({f: "wrong_synthetic"}), "FAIL", "BOOKING_TARGET_MISMATCH")
    add("wrong_timezone", lambda b: b["after_snapshot"]["appointments"][0].update(startTime="2026-10-03T09:00:00+00:00", endTime="2026-10-03T10:00:00+00:00"), "FAIL", "BOOKING_TIMEZONE_MISMATCH")
    add("wrong_slot", lambda b: b["after_snapshot"]["appointments"][0].update(startTime="2026-10-03T11:00:00-04:00", endTime="2026-10-03T12:00:00-04:00"), "FAIL", "BOOKING_TIME_MISMATCH")

    def duplicate(b):
        second = {**b["after_snapshot"]["appointments"][0], "id": "apt_synthetic_2"}
        b["after_snapshot"]["appointments"].append(second)
        trace = copy.deepcopy(b["call"]["transcript_with_tool_calls"])
        for row in trace:
            row["tool_call_id"] = "tool_2"
        trace[1]["content"] = json.dumps({"success": True, "appointmentId": second["id"]})
        b["call"]["transcript_with_tool_calls"].extend(trace)

    add("duplicate_durable_retry", duplicate, "FAIL", "DUPLICATE_ACTIVE_TARGET")
    for status in ("cancelled", "invalid"):
        add(status + "_booking", lambda b, s=status: b["after_snapshot"]["appointments"][0].update(status=s), "FAIL", "BOOKING_NOT_ACTIVE")
    add("unknown_backend_status", lambda b: b["after_snapshot"]["appointments"][0].update(status="provider_unknown"), "UNKNOWN", "APPOINTMENT_STATUS_UNKNOWN")
    add("incomplete_after", lambda b: b["after_snapshot"].update(complete=False), "UNKNOWN", "INCOMPLETE_BACKEND_SNAPSHOT")
    add("stale_after", lambda b: b["after_snapshot"].update(captured_at="2026-10-01T15:00:00-04:00"), "UNKNOWN", "STALE_SNAPSHOT")
    add("stale_before", lambda b: b["before_snapshot"].update(captured_at="2026-10-01T12:00:00-04:00"), "UNKNOWN", "STALE_SNAPSHOT")
    add("capture_before_call_end", lambda b: b["after_snapshot"].update(captured_at="2026-10-01T14:01:00-04:00"), "UNKNOWN", "SNAPSHOT_WRONG_TIME_ORDER")
    add("naive_contract_datetime", lambda b: b["contract"]["expected"].update(startTime="2026-10-03T09:00:00"), "UNKNOWN", "NAIVE_DATETIME")
    add("missing_call", lambda b: b.pop("call"), "UNKNOWN", "MISSING_CALL")
    add("endless_call", lambda b: b["call"].update(call_status="ongoing"), "UNKNOWN", "CALL_NOT_ENDED")
    add("missing_end_timestamp", lambda b: b["call"].pop("end_timestamp"), "UNKNOWN", "CALL_TIMING_MISSING")
    add("missing_before", lambda b: b.pop("before_snapshot"), "UNKNOWN", "MISSING_SNAPSHOT")
    add("preexisting_not_new", lambda b: b["before_snapshot"].update(appointments=copy.deepcopy(b["after_snapshot"]["appointments"])), "FAIL", "PREEXISTING_BOOKING_NOT_NEW")
    add("missing_tool_result", lambda b: b["call"]["transcript_with_tool_calls"].pop(), "UNKNOWN", "MISSING_TOOL_RESULT")
    add("bad_tool_arguments", lambda b: b["call"]["transcript_with_tool_calls"][0].update(arguments="not-json"), "UNKNOWN", "TOOL_ARGUMENTS_INVALID_JSON")
    add("unreviewed_claim", lambda b: b["call"]["observation"].update(reviewed=False), "UNKNOWN", "CLAIM_NOT_REVIEWED")
    add("unknown_result_projection", lambda b: b["call"]["transcript_with_tool_calls"][1].update(content=json.dumps({"id": "raw_get_id_not_a_result_schema"})), "UNKNOWN", "TOOL_RESULT_SCHEMA_UNKNOWN")

    def reject(b):
        b["contract"]["operation"] = "reject"
        b["call"]["observation"]["claimed_booking"] = False
        b["call"]["transcript_with_tool_calls"] = []
        b["after_snapshot"]["appointments"] = []

    add("eligible_rejection", reject, "PASS")
    add("ineligible_case_booked", lambda b: b["contract"].update(operation="reject"), "FAIL", "INELIGIBLE_CASE_BOOKED")

    def safe_retry(b):
        trace = copy.deepcopy(b["call"]["transcript_with_tool_calls"])
        for row in trace:
            row["tool_call_id"] = "tool_2"
        b["call"]["transcript_with_tool_calls"].extend(trace)

    add("retry_one_durable_record", safe_retry, "PASS", "RETRY_OBSERVED")

    def recovered_retry(b):
        safe_retry(b)
        b["call"]["transcript_with_tool_calls"][1]["content"] = json.dumps({"success": False})

    add("failed_attempt_recovered", recovered_retry, "PASS", "FAILED_ATTEMPT_RECOVERED")
    add("same_instant_utc", lambda b: b["after_snapshot"]["appointments"][0].update(startTime="2026-10-03T13:00:00+00:00", endTime="2026-10-03T14:00:00+00:00"), "PASS")

    def allow_existing(b):
        b["contract"]["allow_existing"] = True
        b["before_snapshot"]["appointments"] = copy.deepcopy(b["after_snapshot"]["appointments"])

    add("explicit_existing_allowed", allow_existing, "PASS")

    def old_plus_new(b):
        old = {**b["after_snapshot"]["appointments"][0], "id": "apt_synthetic_old"}
        b["before_snapshot"]["appointments"] = [copy.deepcopy(old)]
        b["after_snapshot"]["appointments"].append(old)

    add("preexisting_plus_new_duplicate", old_plus_new, "FAIL", "DUPLICATE_ACTIVE_TARGET")
    add("provider_failure_body_success", lambda b: b["call"]["transcript_with_tool_calls"][1].update(successful=False), "FAIL", "TOOL_RESULT_SUCCESS_CONTRADICTION")
    add("provider_flag_not_boolean", lambda b: b["call"]["transcript_with_tool_calls"][1].update(successful="false"), "UNKNOWN", "TOOL_RESULT_ENVELOPE_INVALID")
    add("result_precedes_invocation", lambda b: b["call"]["transcript_with_tool_calls"].reverse(), "UNKNOWN", "TOOL_RESULT_PRECEDES_INVOCATION")
    for index, value in enumerate((None, "", False, 1, [])):
        def malformed_tool(b, malformed=value):
            b["contract"]["booking_tool_name"] = malformed
            b["call"]["transcript_with_tool_calls"][0]["name"] = malformed
        add("malformed_tool_name_" + str(index), malformed_tool, "UNKNOWN", "BOOKING_TOOL_NAME_INVALID")

    def reject_changed(b, change):
        b["contract"]["operation"] = "reject"
        b["call"]["observation"]["claimed_booking"] = False
        b["call"]["transcript_with_tool_calls"] = []
        prior = copy.deepcopy(b["after_snapshot"]["appointments"][0])
        prior.update(change)
        b["before_snapshot"]["appointments"] = [prior]

    add("reject_reactivated_existing", lambda b: reject_changed(b, {"status": "cancelled"}), "FAIL", "INELIGIBLE_CASE_BOOKED")
    add("reject_reassigned_existing", lambda b: reject_changed(b, {"contactId": "other_contact"}), "FAIL", "INELIGIBLE_CASE_BOOKED")
    add("reject_rescheduled_existing", lambda b: reject_changed(b, {"startTime": "2026-10-03T11:00:00-04:00", "endTime": "2026-10-03T12:00:00-04:00"}), "FAIL", "INELIGIBLE_CASE_BOOKED")
    add("malformed_trace_role", lambda b: b["call"]["transcript_with_tool_calls"].insert(0, {"role": {"invalid": True}}), "UNKNOWN", "TOOL_TRACE_SCHEMA_UNKNOWN")
    return result
