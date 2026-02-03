"""Tests for configuration module."""

import os
import tempfile
import pytest

from config import Config, ConfigError


class TestEnvVarSubstitution:
    """Tests for environment variable substitution."""

    def test_substitutes_env_var(self):
        """Substitutes ${VAR} with env value."""
        os.environ['TEST_VAR'] = 'test_value'
        try:
            config_content = "key: ${TEST_VAR}"
            with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
                f.write(config_content)
                config_path = f.name

            try:
                config = Config(config_path)
                assert config.get('key') == 'test_value'
            finally:
                os.unlink(config_path)
        finally:
            del os.environ['TEST_VAR']

    def test_uses_default_when_unset(self):
        """Uses default value when env var not set."""
        # Ensure var is not set
        if 'UNSET_VAR' in os.environ:
            del os.environ['UNSET_VAR']

        config_content = "key: ${UNSET_VAR:-default_value}"
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_content)
            config_path = f.name

        try:
            config = Config(config_path)
            assert config.get('key') == 'default_value'
        finally:
            os.unlink(config_path)

    def test_env_overrides_default(self):
        """Env value overrides default."""
        os.environ['OVERRIDE_VAR'] = 'env_value'
        try:
            config_content = "key: ${OVERRIDE_VAR:-default_value}"
            with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
                f.write(config_content)
                config_path = f.name

            try:
                config = Config(config_path)
                assert config.get('key') == 'env_value'
            finally:
                os.unlink(config_path)
        finally:
            del os.environ['OVERRIDE_VAR']


class TestDotNotation:
    """Tests for dot notation access."""

    def test_get_nested_value(self):
        """Gets nested value using dot notation."""
        config_content = """
app:
  server:
    port: 5000
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_content)
            config_path = f.name

        try:
            config = Config(config_path)
            assert config.get('app.server.port') == 5000
        finally:
            os.unlink(config_path)

    def test_get_returns_default(self):
        """Returns default for missing key."""
        config_content = "key: value"
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_content)
            config_path = f.name

        try:
            config = Config(config_path)
            assert config.get('missing.key', 'default') == 'default'
        finally:
            os.unlink(config_path)

    def test_set_nested_value(self):
        """Sets nested value using dot notation."""
        config_content = """
app:
  port: 5000
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_content)
            config_path = f.name

        try:
            config = Config(config_path)
            config.set('app.host', 'localhost')
            assert config.get('app.host') == 'localhost'
            assert config.get('app.port') == 5000  # Unchanged
        finally:
            os.unlink(config_path)


class TestValidation:
    """Tests for configuration validation."""

    def test_validates_required_fields(self):
        """Raises on missing required fields."""
        config_content = """
app:
  port: 5000
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_content)
            config_path = f.name

        try:
            config = Config(config_path)
            with pytest.raises(ConfigError) as exc_info:
                config.validate()
            assert 'database.url' in str(exc_info.value)
        finally:
            os.unlink(config_path)


class TestToDict:
    """Tests for dictionary conversion."""

    def test_masks_sensitive_values(self):
        """Masks sensitive values in output."""
        config_content = """
app:
  api_key: secret123456
  password: mypassword
  name: public_name
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_content)
            config_path = f.name

        try:
            config = Config(config_path)
            result = config.to_dict()
            assert '****' in result['app']['api_key']
            assert '****' in result['app']['password']
            assert result['app']['name'] == 'public_name'
        finally:
            os.unlink(config_path)


class TestFileHandling:
    """Tests for file handling."""

    def test_raises_on_missing_file(self):
        """Raises ConfigError for missing file."""
        with pytest.raises(ConfigError) as exc_info:
            Config('/nonexistent/path/config.yaml')
        assert 'not found' in str(exc_info.value)

    def test_raises_on_invalid_yaml(self):
        """Raises ConfigError for invalid YAML."""
        config_content = "invalid: yaml: content: [["
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_content)
            config_path = f.name

        try:
            with pytest.raises(ConfigError) as exc_info:
                Config(config_path)
            assert 'Invalid YAML' in str(exc_info.value)
        finally:
            os.unlink(config_path)


class TestUpdate:
    """Tests for configuration updates."""

    def test_update_multiple_values(self):
        """Updates multiple values at once."""
        config_content = """
app:
  port: 5000
  debug: false
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_content)
            config_path = f.name

        try:
            config = Config(config_path)
            config.update({
                'app.port': 8080,
                'app.debug': True
            })
            assert config.get('app.port') == 8080
            assert config.get('app.debug') is True
        finally:
            os.unlink(config_path)
