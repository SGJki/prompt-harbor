# Test coverage matrix

当前验收基线（2026-09-18）：`uv run pytest --collect-only -q` 为 **92 tests collected**；loopback-enabled 环境中的 `uv run pytest -q` 为 **92 passed, 0 skipped, 0 xfailed**。当前沙箱禁止 loopback，因此本次回归按项目规则在沙箱外完成；浏览器先执行 `npm ci`，再用 Playwright 默认 Chromium，或设置 `PROMPT_HARBOR_BROWSER` 指向已安装浏览器。

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
| pi-messages sidecar forwarding, usage and failure lifecycle | `tests/test_pi_messages.py` |
| CLI metadata without body | `tests/test_cli.py::test_list_has_metadata_columns`, `tests/test_security.py::test_list_does_not_print_body` |
| Detail API, static resources and traversal protection | `tests/test_ui_static.py` all four tests; `tests/test_security.py::test_show_prints_payload`, `test_show_headers_are_json` |
| Startup and request-level invalid configuration behavior | `tests/test_boundaries.py::test_invalid_max_body_is_rejected_at_startup`, `tests/test_iteration3.py::test_request_level_body_limit_error_is_json_and_terminal` cover startup rejection and terminal JSON 400 handling |
| Security and payload preservation | `tests/test_security.py` all five tests; `tests/test_schema.py::test_sensitive_header_names_all_redacted` |
| Periodic retention worker and shutdown ownership | `tests/test_iteration3.py::test_periodic_purge_cascades_and_server_stays_live` |
| Loopback-only listener boundary | `tests/test_iteration3.py::test_listener_rejects_non_loopback_before_bind`, `test_listener_accepts_default_and_explicit_loopback` |
| Request-level invalid body-limit handling | `tests/test_iteration3.py::test_request_level_body_limit_error_is_json_and_terminal[0]`, `[-1]`, `[not-an-integer]` |
| Canonical module entrypoints | `tests/test_iteration3.py::test_all_public_entrypoints_share_canonical_behavior` |
| Direct SQLite calls/sessions SSE invalidation | `tests/test_iteration3.py::test_sse_notifies_direct_sql_calls_and_sessions` |
| Sidecar credential redaction on request-level config failure | `tests/test_iteration3.py::test_sidecar_configuration_error_persists_only_scrubbed_body` |
| Real browser UI workflow | `browser-tests/audit.spec.js` via `npm run test:browser` |

Reverse validation evidence for notification, header passthrough, cleanup and cancellation is recorded in `iteration/iteration-2/PROGRESS.md`; defects and untested follow-up scope are in `TEST_REVIEW_FOLLOWUP.md`.
