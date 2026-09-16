# Test coverage matrix

| SPEC area | Tests |
|---|---|
| Schema tables, integer autoincrement keys, no FKs, indexes | `tests/test_schema.py::test_init_creates_all_tables`, `test_primary_keys_autoincrement`, `test_no_foreign_keys`, `test_required_index` |
| Startup session and shared session across calls | `tests/test_gateway.py::test_multiple_requests_share_startup_session` |
| Configuration and CLI help/init/list/show | `tests/test_schema.py::test_help_mentions_commands`, `test_init_cli`, `test_list_empty`, `test_show_missing_nonzero`, `test_env_database` |
| Usage extraction (JSON/SSE/missing) | `tests/test_schema.py::test_usage_json`, `test_usage_sse`, `test_usage_missing` |
| Retention and business cascade | `tests/test_retention.py` |
| Security redaction and payload visibility | `tests/test_security.py` |
| CLI exit codes and purge/list behavior | `tests/test_cli.py` |
| CLI argument/environment precedence | `tests/test_cli.py::test_cli_argument_overrides_environment`, `test_environment_overrides_default` |
| Body storage limits and truncation flags | `tests/test_gateway.py::test_storage_limit_sets_truncated_flags`; CLI indicator in `agent_gateway_pkg/core.py show` |
| Real HTTP JSON/SSE forwarding, auth handling, persistence | `tests/test_gateway.py::test_sse_capture_and_auth`, `test_json_transparent_forward` |
| Upstream 400/401/429/500 and connection failure | `tests/test_gateway.py::test_upstream_error_status_preserved`, `test_upstream_connection_failure_returns_502` |
| Upstream partial response detection | `tests/test_gateway.py::test_upstream_disconnect_records_partial_failure` |
| Client cancellation and gateway survival | `tests/test_gateway.py::test_client_disconnect_does_not_crash_gateway` |
| Remaining error, cancellation, truncation, and full CLI detail requirements | Verified in loopback-enabled environment: `uv run pytest -q` → 50 passed, 0 skipped. |

当前验收基线：50 tests collected，50 passed，0 skipped，0 xfailed。
