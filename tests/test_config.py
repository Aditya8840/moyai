from app.config import Settings


def test_project_gateway_key_overrides_unrelated_shell_key(tmp_path, monkeypatch):
    monkeypatch.setenv("LITELLM_API_KEY", "unrelated-shell-key")
    env = tmp_path / ".env"
    env.write_text("LITELLM_API_KEY=project-key\n")
    assert Settings(_env_file=env).litellm_api_key == "project-key"
    env.write_text("LITELLM_API_KEY=\n")
    assert Settings(_env_file=env).litellm_api_key == ""
    assert Settings(_env_file=None).litellm_api_key == "unrelated-shell-key"
