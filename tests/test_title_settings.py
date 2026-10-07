from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app


def test_title_setting_persists_validates_and_requires_admin(tmp_path,monkeypatch):
    settings=Settings(_env_file=None,data_dir=tmp_path,auto_prepare_repositories=False,session_titles_enabled=False)
    app=create_app(settings)
    with TestClient(app,base_url=settings.public_url,client=('127.0.0.1',50000)) as client:
        session=client.get('/api/session').json()
        client.headers.update({'Origin':settings.public_url,'X-CSRF-Token':session['csrf']})
        route='/api/settings/session-titles'
        assert client.get(route).json()['model']=='openai/gpt-4.1-nano'
        assert client.put(route,json={'model':'openai/gpt-4o-mini'}).status_code==200
        assert client.get(route).json()['model']=='openai/gpt-4o-mini'
        assert app.state.session_titles.make_agent().model.model=='openai/gpt-4o-mini'
        assert client.put(route,json={'model':'bad\nmodel'}).status_code==422
        client.headers.pop('X-CSRF-Token')
        assert client.put(route,json={'model':'x'}).status_code==403
        client.headers['X-CSRF-Token']=session['csrf']
        original=app.state.security.require
        def member(request,**kwargs):
            from fastapi import HTTPException
            if kwargs.get('admin'):raise HTTPException(403,'Admin required')
            return original(request,**kwargs)
        monkeypatch.setattr(app.state.security,'require',member)
        assert client.put(route,json={'model':'x'}).status_code==403
    restarted=create_app(settings)
    assert restarted.state.session_titles.model_name()=='openai/gpt-4o-mini'
