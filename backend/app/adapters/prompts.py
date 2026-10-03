"""Prompt template system with versioning and validation."""

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Type

import yaml
from pydantic import BaseModel, Field


# ============================================================================
# Exceptions
# ============================================================================


class PromptInjectionDetected(Exception):
    """Exception raised when prompt injection is detected."""

    pass


class TemplateValidationError(Exception):
    """Exception raised when template validation fails."""

    pass


# ============================================================================
# Models
# ============================================================================


class FewShotExample(BaseModel):
    """Few-shot example for prompt template."""

    input: dict
    output: dict
    explanation: Optional[str] = None


class PromptTemplateMetadata(BaseModel):
    """Metadata for prompt template."""

    template_id: str
    version: str
    agent_name: str
    created_at: datetime
    updated_at: datetime
    author: str
    description: str
    tags: list[str] = Field(default_factory=list)


class RenderedPrompt(BaseModel):
    """Rendered prompt ready for LLM invocation."""

    system_prompt: str
    user_prompt: str
    template_id: str
    template_version: str
    rendered_at: datetime
    input_hash: str


# ============================================================================
# Input Sanitizer
# ============================================================================


class InputSanitizer:
    """Sanitize inputs to prevent prompt injection."""

    # Patterns that indicate potential injection
    INJECTION_PATTERNS = [
        r"<\|im_start\|>",  # ChatML delimiters
        r"<\|im_end\|>",
        r"###\s*System:",  # System instruction overrides
        r"###\s*Assistant:",
        r"Human:",
        r"AI:",
        r"\[INST\]",  # Instruction delimiters
        r"\[/INST\]",
        r"<s>",  # Special tokens
        r"</s>",
    ]

    @classmethod
    def sanitize(cls, input_text: str) -> str:
        """
        Sanitize input text to prevent injection.

        Args:
            input_text: Text to sanitize

        Returns:
            Sanitized text

        Raises:
            PromptInjectionDetected: If injection pattern detected
        """
        # Check for injection patterns
        for pattern in cls.INJECTION_PATTERNS:
            if re.search(pattern, input_text, re.IGNORECASE):
                raise PromptInjectionDetected(f"Detected injection pattern: {pattern}")

        # Escape special characters
        sanitized = input_text.replace("\\", "\\\\")
        sanitized = sanitized.replace('"', '\\"')

        return sanitized

    @classmethod
    def validate_template_inputs(cls, inputs: dict) -> None:
        """
        Validate inputs against injection patterns.

        Args:
            inputs: Dictionary of template inputs

        Raises:
            PromptInjectionDetected: If injection detected in any input
        """
        for key, value in inputs.items():
            if isinstance(value, str):
                cls.sanitize(value)
            elif isinstance(value, dict):
                cls.validate_template_inputs(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        cls.sanitize(item)
                    elif isinstance(item, dict):
                        cls.validate_template_inputs(item)


# ============================================================================
# Prompt Template
# ============================================================================


class PromptTemplate:
    """Versioned prompt template with validation."""

    def __init__(
        self,
        template_id: str,
        version: str,
        agent_name: str,
        system_prompt: str,
        user_prompt_template: str,
        few_shot_examples: list[dict],
        output_schema: Optional[Type[BaseModel]] = None,
        grounding_instructions: Optional[str] = None,
        confidence_instructions: Optional[str] = None,
        metadata: Optional[PromptTemplateMetadata] = None,
    ):
        self.template_id = template_id
        self.version = version
        self.agent_name = agent_name
        self.system_prompt = system_prompt
        self.user_prompt_template = user_prompt_template
        self.few_shot_examples = [FewShotExample(**ex) for ex in few_shot_examples]
        self.output_schema = output_schema
        self.grounding_instructions = grounding_instructions
        self.confidence_instructions = confidence_instructions
        self.metadata = metadata

    def render(self, inputs: dict) -> RenderedPrompt:
        """
        Render template with inputs and validation.

        Args:
            inputs: Dictionary of template inputs

        Returns:
            RenderedPrompt with system and user prompts

        Raises:
            PromptInjectionDetected: If injection detected
            TemplateValidationError: If template rendering fails
        """
        # Validate inputs
        validation_result = self.validate_inputs(inputs)
        if not validation_result["is_valid"]:
            raise TemplateValidationError(
                f"Input validation failed: {validation_result['errors']}"
            )

        # Render user prompt
        try:
            user_prompt = self._render_template(self.user_prompt_template, inputs)
        except Exception as e:
            raise TemplateValidationError(f"Failed to render template: {str(e)}")

        # Calculate input hash
        input_hash = hashlib.sha256(str(inputs).encode()).hexdigest()[:16]

        return RenderedPrompt(
            system_prompt=self.system_prompt,
            user_prompt=user_prompt,
            template_id=self.template_id,
            template_version=self.version,
            rendered_at=datetime.now(),
            input_hash=input_hash,
        )

    def validate_inputs(self, inputs: dict) -> dict:
        """
        Validate inputs against template schema.

        Args:
            inputs: Dictionary of template inputs

        Returns:
            Dictionary with is_valid and errors
        """
        errors = []

        try:
            # Check for injection patterns
            InputSanitizer.validate_template_inputs(inputs)
        except PromptInjectionDetected as e:
            errors.append(str(e))

        # Check required template variables
        required_vars = self._extract_template_variables(self.user_prompt_template)
        missing_vars = [var for var in required_vars if var not in inputs]
        if missing_vars:
            errors.append(f"Missing required variables: {missing_vars}")

        return {"is_valid": len(errors) == 0, "errors": errors}

    def _render_template(self, template: str, inputs: dict) -> str:
        """
        Render template string with inputs.

        Simple variable substitution using {{ variable }} syntax.
        """
        rendered = template
        for key, value in inputs.items():
            placeholder = f"{{{{{key}}}}}"
            if isinstance(value, (dict, list)):
                import json

                value_str = json.dumps(value, indent=2)
            else:
                value_str = str(value)
            rendered = rendered.replace(placeholder, value_str)
        return rendered

    def _extract_template_variables(self, template: str) -> list[str]:
        """Extract variable names from template."""
        pattern = r"\{\{(\w+)\}\}"
        return re.findall(pattern, template)

    @classmethod
    def load_from_yaml(cls, yaml_path: Path) -> "PromptTemplate":
        """
        Load template from YAML file.

        Args:
            yaml_path: Path to YAML template file

        Returns:
            PromptTemplate instance
        """
        with open(yaml_path, "r") as f:
            data = yaml.safe_load(f)

        metadata = PromptTemplateMetadata(
            template_id=data["template_id"],
            version=data["version"],
            agent_name=data["agent_name"],
            created_at=datetime.fromisoformat(data.get("created_at", datetime.now().isoformat())),
            updated_at=datetime.fromisoformat(data.get("updated_at", datetime.now().isoformat())),
            author=data.get("author", "unknown"),
            description=data.get("description", ""),
            tags=data.get("tags", []),
        )

        return cls(
            template_id=data["template_id"],
            version=data["version"],
            agent_name=data["agent_name"],
            system_prompt=data["system_prompt"],
            user_prompt_template=data["user_prompt_template"],
            few_shot_examples=data.get("few_shot_examples", []),
            grounding_instructions=data.get("grounding_instructions"),
            confidence_instructions=data.get("confidence_instructions"),
            metadata=metadata,
        )

    @classmethod
    def load_from_registry(cls, template_id: str, version: str = "latest") -> "PromptTemplate":
        """
        Load template from registry.

        Args:
            template_id: Template identifier
            version: Template version or "latest"

        Returns:
            PromptTemplate instance
        """
        # Determine registry path
        registry_path = Path("prompts")
        if not registry_path.exists():
            repo_candidate = Path(__file__).resolve().parents[3] / "prompts"
            if repo_candidate.exists():
                registry_path = repo_candidate
            elif Path(".kiro/prompts").exists():
                registry_path = Path(".kiro/prompts")
        agent_dir = registry_path / template_id.rsplit("-", 1)[0]

        if version == "latest":
            # Follow symlink to latest version
            latest_link = agent_dir / "latest"
            if latest_link.exists() and latest_link.is_symlink():
                yaml_path = latest_link
            else:
                # Find highest version
                versions = sorted(
                    [f for f in agent_dir.glob("v*.yaml")],
                    key=lambda p: p.stem,
                    reverse=True,
                )
                if not versions:
                    raise FileNotFoundError(f"No templates found for {template_id}")
                yaml_path = versions[0]
        else:
            yaml_path = agent_dir / f"{version}.yaml"

        if not yaml_path.exists():
            raise FileNotFoundError(f"Template not found: {yaml_path}")

        return cls.load_from_yaml(yaml_path)


# ============================================================================
# Prompt Template Loader
# ============================================================================


class PromptTemplateLoader:
    """Loader for prompt templates from YAML files."""

    def __init__(self):
        self.templates_cache: dict[str, dict] = {}

    def load_from_file(self, yaml_path: Path) -> dict:
        """
        Load all templates from a YAML file.

        Args:
            yaml_path: Path to YAML template file

        Returns:
            Dictionary of template_name -> template_data
        """
        if not yaml_path.exists():
            repo_root_candidate = Path(__file__).resolve().parents[3] / yaml_path
            if repo_root_candidate.exists():
                yaml_path = repo_root_candidate
            else:
                path_str = str(yaml_path)
                if ".kiro/prompts/" in path_str:
                    prompts_candidate = Path(__file__).resolve().parents[3] / path_str.replace(".kiro/prompts/", "prompts/")
                    if prompts_candidate.exists():
                        yaml_path = prompts_candidate

        with open(yaml_path, "r") as f:
            data = yaml.safe_load(f)

        # Cache templates
        templates = data.get("templates", {})
        self.templates_cache[str(yaml_path)] = templates

        return templates

    def render_template(self, template: dict, inputs: dict) -> RenderedPrompt:
        """
        Render a template with inputs.

        Args:
            template: Template dictionary from YAML
            inputs: Dictionary of template inputs

        Returns:
            RenderedPrompt with system and user prompts
        """
        # Validate inputs
        InputSanitizer.validate_template_inputs(inputs)

        # Render system prompt
        system_prompt = template.get("system_prompt", "")

        # Render user prompt
        user_prompt_template = template.get("user_prompt", "")
        user_prompt = self._render_template_string(user_prompt_template, inputs)

        # Calculate input hash
        input_hash = hashlib.sha256(str(inputs).encode()).hexdigest()[:16]

        return RenderedPrompt(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            template_id=template.get("template_id", "unknown"),
            template_version=template.get("version", "unknown"),
            rendered_at=datetime.now(),
            input_hash=input_hash,
        )

    def _render_template_string(self, template: str, inputs: dict) -> str:
        """
        Render template string with inputs.

        Supports:
        - Simple variable substitution: {{variable}}
        - Handlebars-style iteration: {{#each items}}...{{/each}}
        """
        rendered = template

        # Handle {{#each}} blocks
        each_pattern = r"\{\{#each\s+(\w+)\}\}(.*?)\{\{/each\}\}"
        for match in re.finditer(each_pattern, template, re.DOTALL):
            var_name = match.group(1)
            block_template = match.group(2)

            if var_name in inputs and isinstance(inputs[var_name], list):
                rendered_blocks = []
                for item in inputs[var_name]:
                    # Render block with item context
                    block_rendered = block_template
                    if isinstance(item, dict):
                        for key, value in item.items():
                            placeholder = f"{{{{this.{key}}}}}"
                            block_rendered = block_rendered.replace(placeholder, str(value))
                    else:
                        block_rendered = block_rendered.replace("{{this}}", str(item))
                    rendered_blocks.append(block_rendered)

                rendered = rendered.replace(match.group(0), "\n".join(rendered_blocks))
            else:
                # Remove block if variable not found
                rendered = rendered.replace(match.group(0), "")

        # Handle simple {{variable}} substitution
        for key, value in inputs.items():
            placeholder = f"{{{{{key}}}}}"
            if isinstance(value, (dict, list)):
                import json

                value_str = json.dumps(value, indent=2)
            else:
                value_str = str(value)
            rendered = rendered.replace(placeholder, value_str)

        return rendered
