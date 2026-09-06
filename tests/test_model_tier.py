"""
M14 本地小模型分级：model_provider 分级通道与结构化入口装配（全离线）

覆盖：通道环境解析（fast 未配置透明回退 main、独立模型/提供商覆盖、Azure 部署覆盖、
非法 tier 拒绝）、create_chat_model 按 tier 构造、控制器分级装配——
主管分类/咨询判定/槽位抽取走 fast，生成类（话术/RAG 回答）走 main。
"""

import pytest

import config.model_provider as mp
from agents.appointment_agent import AppointmentAgent
from agents.consultant_agent import ConsultantAgent
from agents.task_classification_agent import TaskClassificationAgent
from conftest import FakeChatModel


@pytest.fixture
def clean_tier_env(monkeypatch):
    """清除分级相关环境变量，保证各用例独立"""
    for key in ('LLM_FAST_MODEL', 'MODEL_FAST_PROVIDER', 'AZURE_FAST_DEPLOYMENT',
                'LLM_MODEL', 'MODEL_PROVIDER', 'AZURE_OPENAI_DEPLOYMENT'):
        monkeypatch.delenv(key, raising=False)


class TestResolveChannel:
    def test_fast_without_config_falls_back_to_main(self, clean_tier_env, monkeypatch):
        monkeypatch.setenv('MODEL_PROVIDER', 'qwen')
        monkeypatch.setenv('LLM_MODEL', 'qwen-max')
        main_cfg = mp.resolve_channel('main')
        fast_cfg = mp.resolve_channel('fast')
        assert fast_cfg['tier'] == 'fast'
        assert fast_cfg['provider'] == main_cfg['provider'] == 'qwen'
        assert fast_cfg['model'] == main_cfg['model'] == 'qwen-max'  # 透明回退

    def test_fast_model_override(self, clean_tier_env, monkeypatch):
        monkeypatch.setenv('MODEL_PROVIDER', 'qwen')
        monkeypatch.setenv('LLM_MODEL', 'qwen-max')
        monkeypatch.setenv('LLM_FAST_MODEL', 'qwen-turbo')
        assert mp.resolve_channel('main')['model'] == 'qwen-max'
        assert mp.resolve_channel('fast')['model'] == 'qwen-turbo'

    def test_fast_provider_override(self, clean_tier_env, monkeypatch):
        monkeypatch.setenv('MODEL_PROVIDER', 'qwen')
        monkeypatch.setenv('MODEL_FAST_PROVIDER', 'deepseek')
        monkeypatch.setenv('LLM_FAST_MODEL', 'deepseek-tiny')
        cfg = mp.resolve_channel('fast')
        assert cfg['provider'] == 'deepseek'
        assert cfg['model'] == 'deepseek-tiny'

    def test_azure_deployment_override_and_fallback(self, clean_tier_env, monkeypatch):
        monkeypatch.setenv('MODEL_PROVIDER', 'azure')
        monkeypatch.setenv('AZURE_OPENAI_DEPLOYMENT', 'gpt-4o-main')
        assert mp.resolve_channel('fast')['deployment'] == 'gpt-4o-main'  # 未配置 → 回退 main
        monkeypatch.setenv('AZURE_FAST_DEPLOYMENT', 'gpt-4o-mini-fast')
        assert mp.resolve_channel('fast')['deployment'] == 'gpt-4o-mini-fast'
        assert mp.resolve_channel('main')['deployment'] == 'gpt-4o-main'

    def test_invalid_tier_rejected(self, clean_tier_env):
        with pytest.raises(ValueError):
            mp.resolve_channel('ultra')

    def test_channel_label_readable(self, clean_tier_env, monkeypatch):
        monkeypatch.setenv('MODEL_PROVIDER', 'qwen')
        monkeypatch.setenv('LLM_MODEL', 'qwen-max')
        monkeypatch.setenv('LLM_FAST_MODEL', 'qwen-turbo')
        assert mp.channel_label('fast') == 'fast:qwen/qwen-turbo'
        assert mp.channel_label('main') == 'main:qwen/qwen-max'


class TestCreateChatModelTier:
    def test_openai_compatible_tier_builds_different_models(self, clean_tier_env, monkeypatch):
        monkeypatch.setenv('MODEL_PROVIDER', 'qwen')
        monkeypatch.setenv('LLM_API_KEY', 'offline-test-key')
        monkeypatch.setenv('LLM_MODEL', 'qwen-max')
        monkeypatch.setenv('LLM_FAST_MODEL', 'qwen-turbo')
        main_model = mp.create_chat_model(temperature=0)
        fast_model = mp.create_chat_model(temperature=0, tier='fast')
        assert main_model.model_name == 'qwen-max'
        assert fast_model.model_name == 'qwen-turbo'

    def test_fast_unconfigured_constructs_main_equivalent(self, clean_tier_env, monkeypatch):
        monkeypatch.setenv('MODEL_PROVIDER', 'qwen')
        monkeypatch.setenv('LLM_API_KEY', 'offline-test-key')
        monkeypatch.setenv('LLM_MODEL', 'qwen-max')
        fast_model = mp.create_chat_model(temperature=0, tier='fast')
        assert fast_model.model_name == 'qwen-max'

    def test_azure_tier_uses_fast_deployment(self, clean_tier_env, monkeypatch):
        monkeypatch.setenv('MODEL_PROVIDER', 'azure')
        monkeypatch.setenv('AZURE_OPENAI_API_KEY', 'offline-test-key')
        monkeypatch.setenv('AZURE_OPENAI_ENDPOINT', 'https://offline-test.openai.azure.com/')
        monkeypatch.setenv('AZURE_OPENAI_VERSION', '2024-02-01')
        monkeypatch.setenv('AZURE_OPENAI_DEPLOYMENT', 'gpt-4o-main')
        monkeypatch.setenv('AZURE_FAST_DEPLOYMENT', 'gpt-4o-mini-fast')
        fast_model = mp.create_chat_model(temperature=0, tier='fast')
        assert fast_model.deployment_name == 'gpt-4o-mini-fast'


class TestAgentTierWiring:
    """控制器装配：结构化高频入口（分类/判定/抽取）接 fast 通道，生成类接 main"""

    def _patch_factory(self, monkeypatch, module, calls):
        def fake_factory(temperature=0, tier='main'):
            calls.append((tier, temperature))
            return FakeChatModel()
        monkeypatch.setattr(f"{module}.create_chat_model", fake_factory)

    def test_task_classification_supervisor_uses_fast(self, monkeypatch):
        calls = []
        self._patch_factory(monkeypatch, 'agents.task_classification_agent', calls)
        agent = TaskClassificationAgent(appointment_agent=None, consultant_agent=None)
        assert calls == [('fast', 0)]
        # 分类器与处理器共享同一注册表：工具清单注入分类提示词
        assert '可调用的工具清单' in agent.task_classifier.prompt.template
        assert agent.classification_processor.tool_registry is agent.tool_registry

    def test_appointment_splits_parse_fast_generate_main(self, monkeypatch):
        calls = []
        self._patch_factory(monkeypatch, 'agents.appointment_agent', calls)
        agent = AppointmentAgent(session_id='t1')
        assert calls == [('main', 0), ('fast', 0)]  # 生成在前、结构化抽取在后
        assert agent.input_parser.llm is agent.structured_llm  # 槽位抽取接 fast
        assert agent.appointment_processor.llm is agent.llm      # 话术生成接 main
        assert agent.llm is not agent.structured_llm

    def test_consultant_splits_judge_fast_generate_main(self, monkeypatch):
        calls = []
        self._patch_factory(monkeypatch, 'agents.consultant_agent', calls)
        agent = ConsultantAgent(session_id='t1')
        assert calls == [('main', 0.3), ('fast', 0)]
        assert agent.consultation_classifier.llm is agent.structured_llm  # YES/NO 判定接 fast
        assert agent.response_generator.llm is agent.llm                   # 回答生成接 main
