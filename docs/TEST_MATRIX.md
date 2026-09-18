# Test coverage matrix

当前验收基线：`uv run pytest --collect-only -q` 为 **72 tests collected**；沙箱外 `uv run pytest -q` 为 **72 passed, 0 skipped, 0 xfailed**。

| SPEC / review area | Concrete tests |
|---|---|
| Schema tables, integer autoincrement keys, no FKs, index | `tests/test_schema.py::test_init_creates_all_tables`, `test_primary_keys_autoincrement`, `test_no_foreign_keys`, `test_required_index` |
| Startup session and shared session across calls | `tests/test_gateway.py::test_multiple_requests_share_startup_session` |
| Configuration, CLI help/init/list/show/purge and precedence | `tests/test_schema.py::test_help_mentions_commands`, `test_init_cli`, `test_list_empty`, `test_show_missing_nonzero`, `test_env_database`; `tests/test_cli.py` all six tests, including `test_list_has_metadata_columns` |
| Usage extraction and legacy field mapping | `tests/test_schema.py::test_usage_json`, `test_usage_sse`, `test_usage_missing`; `tests/test_boundaries.py::test_legacy_usage_names_are_returned_by_detail_api` |
| Real model and stream extraction (`true`, `false`, absent) | `tests/test_schema.py::test_model_extraction_shape`, `test_stream_boolean` use real POSTs and persisted `calls` rows |
| Real JSON/SSE forwarding, status and response headers | `tests/test_gateway.py::test_json_transparent_forward`, `test_sse_capture_and_auth`, `test_upstream_error_status_preserved`, `test_gateway_serves_audit_ui_and_overview_api` |
| Live SSE ready → completed POST → invalidate, open subscription | `tests/test_gateway.py::test_sse_invalidate_after_real_post_without_closing_subscription` asserts event name and `resource=calls` twice |
| Upstream header forwarding and secret exclusion | `tests/test_gateway.py::test_forwarded_headers_and_secret_never_persisted_or_logged`, `tests/test_schema.py::test_headers_redacted`, `test_headers_case_insensitive`, security redaction tests |
| Upstream connection failure and partial response | `tests/test_gateway.py::test_upstream_connection_failure_returns_502`, `test_upstream_disconnect_records_partial_failure` |
| Client cancellation terminal lifecycle and survival | `tests/test_gateway.py::test_client_cancel_records_failed_terminal_chain_and_service_survives`, `test_client_disconnect_does_not_crash_gateway` |
| Full retention cascade, recent chain preservation, sessions, idempotence | `tests/test_retention.py::test_purge_removes_full_expired_chain_and_keeps_recent_chain`, `test_purge_repeat_idempotent`, `test_sessions_survive_purge`, `test_business_cascade_without_fk` |
| Startup cleanup after restart using same DB | `tests/test_retention.py::test_startup_purge_cleans_expired_chain_on_restart` |
| Body limit `limit-1`, `limit`, `limit+1`, online bytes and flags | `tests/test_boundaries.py::test_request_limit_boundaries_preserve_online_body_and_store_prefix`, `test_response_limit_boundaries_preserve_online_bytes_and_store_prefix`, `tests/test_gateway.py::test_exact_body_limit_preserves_complete_request_and_response`, `test_storage_limit_sets_truncated_flags` |
| API bad ID and unknown API path | `tests/test_boundaries.py::test_api_id_and_unknown_paths_have_explicit_errors` |
| pi-messages sidecar forwarding, usage and failure lifecycle | `tests/test_pi_messages.py` (同期工作区新增测试) |
| CLI metadata without body | `tests/test_cli.py::test_list_has_metadata_columns`, `tests/test_security.py::test_list_does_not_print_body` |
| Detail API, static resources and traversal protection | `tests/test_ui_static.py` all four tests; `tests/test_security.py::test_show_prints_payload`, `test_show_headers_are_json` |
| Known invalid configuration behavior | `tests/test_boundaries.py::test_invalid_max_body_is_known_defect_but_gateway_process_survives` records current `ValueError` while requiring process survival |
| Security and payload preservation | `tests/test_security.py` all five tests; `tests/test_schema.py::test_sensitive_header_names_all_redacted` |

Reverse validation evidence for notification, header passthrough, cleanup and cancellation is recorded in `PROGRESS.md`; defects and untested follow-up scope are in `TEST_REVIEW_FOLLOWUP.md`.
