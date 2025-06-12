import logging
from pathlib import Path

import pytest
from sensai.util.logging import LOG_DEFAULT_FORMAT, configure

from multilspy.language_server import SyncLanguageServer
from multilspy.multilspy_config import Language, MultilspyConfig
from multilspy.multilspy_logger import MultilspyLogger

configure(level=logging.DEBUG)


@pytest.fixture(scope="session")
def resources_dir() -> Path:
    """Path to the test resources directory."""
    current_dir = Path(__file__).parent
    return current_dir / "resources"


class LanguageParamRequest:
    param: Language


def get_repo_path(language: Language) -> Path:
    return Path(__file__).parent / "resources" / "repos" / language / "test_repo"


def create_ls(language: Language, repo_path: str):
    config = MultilspyConfig(code_language=language)
    logger = MultilspyLogger(log_level=logging.DEBUG)

    # Simple way: Use basicConfig to set format for all handlers
    logging.basicConfig(format=LOG_DEFAULT_FORMAT, level=logging.DEBUG, force=True)  # Override existing configuration

    return SyncLanguageServer.create(config, logger, repo_path)


def create_default_ls(language: Language) -> SyncLanguageServer:
    repo_path = str(get_repo_path(language))
    return create_ls(language, repo_path)


@pytest.fixture(scope="session")
def repo_path(request: LanguageParamRequest) -> Path:
    """Get the repository path for a specific language.

    This fixture requires a language parameter via pytest.mark.parametrize:

    Example:
    ```
    @pytest.mark.parametrize("repo_path", [Language.PYTHON], indirect=True)
    def test_python_repo(repo_path):
        assert (repo_path / "src").exists()
    ```

    """
    if not hasattr(request, "param"):
        raise ValueError("Language parameter must be provided via pytest.mark.parametrize")

    language = request.param
    return get_repo_path(language)


@pytest.fixture(scope="session")
def language_server(request: LanguageParamRequest):
    """Create a SyncLanguageServer instance configured for the specified language.

    This fixture requires a language parameter via pytest.mark.parametrize:

    Example:
    ```
    @pytest.mark.parametrize("language_server", [Language.PYTHON], indirect=True)
    def test_python_server(language_server: SyncLanguageServer) -> None:
        # Use the Python language server
        pass
    ```

    You can also test multiple languages in a single test:
    ```
    @pytest.mark.parametrize("language_server", [Language.PYTHON, Language.TYPESCRIPT], indirect=True)
    def test_multiple_languages(language_server: SyncLanguageServer) -> None:
        # This test will run once for each language
        pass
    ```

    """
    if not hasattr(request, "param"):
        raise ValueError("Language parameter must be provided via pytest.mark.parametrize")

    language = request.param
    server = create_default_ls(language)
    server.start()
    try:
        yield server
    finally:
        server.stop()
