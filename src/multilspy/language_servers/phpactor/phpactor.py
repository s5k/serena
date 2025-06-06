"""
Provides PHP specific instantiation of the LanguageServer class using PHPActor.
"""

import asyncio
import json
import shutil
import logging
import os
import subprocess
import pathlib
import stat
from contextlib import asynccontextmanager
from typing import AsyncIterator

from overrides import override

from multilspy.multilspy_logger import MultilspyLogger
from multilspy.language_server import LanguageServer
from multilspy.lsp_protocol_handler.server import ProcessLaunchInfo
from multilspy.lsp_protocol_handler.lsp_types import InitializeParams
from multilspy.multilspy_config import MultilspyConfig
from multilspy.multilspy_utils import PlatformUtils, PlatformId

# Platform-specific imports
if os.name != 'nt':  # Unix-like systems
    import pwd
else:
    # Dummy pwd module for Windows
    class pwd:
        @staticmethod
        def getpwuid(uid):
            return type('obj', (), {'pw_name': os.environ.get('USERNAME', 'unknown')})()

class PHPActor(LanguageServer):
    """
    Provides PHP specific instantiation of the LanguageServer class using PHPActor.
    """
    
    @override
    def is_ignored_dirname(self, dirname: str) -> bool:
        # For PHP projects, we should ignore:
        # - vendor: third-party dependencies managed by Composer
        # - node_modules: if the project has JavaScript components
        # - cache: commonly used for caching
        return super().is_ignored_dirname(dirname) or dirname in ["packages", "node_modules", "cache", "build", "dist", "dev", "generated", "lib", "m2-hotfixes", "phpserver", "pub", "server", "var"] 

    def setup_runtime_dependencies(self, logger: MultilspyLogger, config: MultilspyConfig) -> str:
        """
        Setup runtime dependencies for PHPActor.
        """
        platform_id = PlatformUtils.get_platform_id()

        valid_platforms = [
            PlatformId.LINUX_x64,
            PlatformId.LINUX_arm64,
            PlatformId.OSX,
            PlatformId.OSX_x64,
            PlatformId.OSX_arm64,
            PlatformId.WIN_x64,
            PlatformId.WIN_arm64,
        ]
        assert platform_id in valid_platforms, f"Platform {platform_id} is not supported for multilspy PHP at the moment"

        with open(os.path.join(os.path.dirname(__file__), "runtime_dependencies.json"), "r") as f:
            d = json.load(f)
            del d["_description"]

        runtime_dependencies = d.get("runtimeDependencies", [])
        phpactor_ls_dir = os.path.join(os.path.dirname(__file__), "static", "phpactor")
        
        # Check if PHP is installed
        is_php_installed = shutil.which('php') is not None
        assert is_php_installed, "PHP is not installed or isn't in PATH. Please install PHP and try again."
        
        # Check if Composer is installed (optional but recommended)
        is_composer_installed = shutil.which('composer') is not None
        if not is_composer_installed:
            logger.log("Composer is not installed. Some features may not work correctly.", logging.WARNING)

        # Install phpactor if not already installed
        if not os.path.exists(phpactor_ls_dir):
            os.makedirs(phpactor_ls_dir, exist_ok=True)
            for dependency in runtime_dependencies:
                # Windows doesn't support the 'user' parameter and doesn't have pwd module
                if PlatformUtils.get_platform_id().value.startswith("win"):
                    subprocess.run(
                        dependency["command"],
                        shell=True,
                        check=True,
                        cwd=phpactor_ls_dir,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                else:
                    # On Unix-like systems, run as non-root user
                    user = pwd.getpwuid(os.getuid()).pw_name
                    subprocess.run(
                        dependency["command"],
                        shell=True,
                        check=True,
                        user=user,
                        cwd=phpactor_ls_dir,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
        
        phpactor_executable_path = os.path.join(phpactor_ls_dir, "bin", "phpactor")
        
        # Make sure the phpactor binary is executable
        if not os.path.exists(phpactor_executable_path):
            raise FileNotFoundError(f"PHPActor executable not found at {phpactor_executable_path}")
        
        # Make the phpactor binary executable on Unix-like systems
        if not os.name == 'nt':
            os.chmod(phpactor_executable_path, os.stat(phpactor_executable_path).st_mode | stat.S_IEXEC)
        
        # Use the language-server command with stdio
        return f"{phpactor_executable_path} language-server"

    def __init__(self, config: MultilspyConfig, logger: MultilspyLogger, repository_root_path: str):
        # Setup runtime dependencies before initializing
        phpactor_cmd = self.setup_runtime_dependencies(logger, config)
        
        super().__init__(
            config,
            logger,
            repository_root_path,
            ProcessLaunchInfo(cmd=phpactor_cmd, cwd=repository_root_path),
            "php"
        )
        self.server_ready = asyncio.Event()
        self.request_id = 0

    def _get_initialize_params(self, repository_absolute_path: str) -> InitializeParams:
        """
        Returns the initialize params for the PHPActor Language Server.
        """
        with open(os.path.join(os.path.dirname(__file__), "initialize_params.json"), "r") as f:
            d = json.load(f)

        del d["_description"]

        d["processId"] = os.getpid()
        assert d["rootPath"] == "$rootPath"
        d["rootPath"] = repository_absolute_path

        assert d["rootUri"] == "$rootUri"
        d["rootUri"] = pathlib.Path(repository_absolute_path).as_uri()

        assert d["workspaceFolders"][0]["uri"] == "$uri"
        d["workspaceFolders"][0]["uri"] = pathlib.Path(repository_absolute_path).as_uri()

        assert d["workspaceFolders"][0]["name"] == "$name"
        d["workspaceFolders"][0]["name"] = os.path.basename(repository_absolute_path)

        return d

    @asynccontextmanager
    async def start_server(self) -> AsyncIterator["PHPActor"]:
        """Start PHPActor server process"""
        async def register_capability_handler(params):
            return

        async def window_log_message(msg):
            self.logger.log(f"LSP: window/logMessage: {msg}", logging.INFO)

        async def do_nothing(params):
            return

        self.server.on_request("client/registerCapability", register_capability_handler)
        self.server.on_notification("window/logMessage", window_log_message)
        self.server.on_notification("$/progress", do_nothing)
        self.server.on_notification("textDocument/publishDiagnostics", do_nothing)

        async with super().start_server():
            self.logger.log("Starting PHPActor server process", logging.INFO)
            await self.server.start()
            initialize_params = self._get_initialize_params(self.repository_root_path)

            self.logger.log(
                "Sending initialize request from LSP client to LSP server and awaiting response",
                logging.INFO,
            )
            init_response = await self.server.send.initialize(initialize_params)
            self.logger.log(
                "After sent initialize params",
                logging.INFO,
            )
            
            # Verify server capabilities
            assert "textDocumentSync" in init_response["capabilities"]
            assert "completionProvider" in init_response["capabilities"]
            assert "definitionProvider" in init_response["capabilities"]

            self.server.notify.initialized({})
            self.completions_available.set()

            # PHPActor server is typically ready immediately after initialization
            self.server_ready.set()
            await self.server_ready.wait()

            yield self

            await self.server.shutdown()
            await self.server.stop()