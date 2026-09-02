#!/usr/bin/env python3
"""Resolve report-style providers and bind enterprise selection to user evidence.

This module is copied beside report_qc.py in every generated package. It accepts a provider-owned
machine-readable profile when one exists. Existing installed styling skills remain compatible through
a conservative derivation from their immutable SKILL.md brand section.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import unicodedata


REPORT_STYLE_SCHEMA = "biomni-report-style/1"
STYLE_DERIVATION_SCHEMA = "biomni-report-style-derivation/1"
STYLE_PROFILE_NAME = "report_style.json"
STYLE_SKILL_NAME = "SKILL.md"
STYLE_PROVIDER_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
HEX_COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}\b|(?P<quote>['\"])[0-9A-Fa-f]{6}(?P=quote)")
HEADING_RE = re.compile(r"^(#{2,6})\s+(.+?)\s*$")
FRONTMATTER_NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)
PRIMARY_MARKER_RE = re.compile(r"primary\s+accent|logo(?:\s+hand\s+mark)?", re.IGNORECASE)
NON_EVIDENT_COLORS = {"#FFFFFF", "#000000"}
STYLE_SELECTION_NOUNS = {"brand", "branding", "skill", "style", "styling", "template"}
STYLE_SELECTION_ACTIONS = {
    "apply", "applied", "choose", "chosen", "request", "requested", "select", "selected",
    "use", "used", "using", "want", "wanted", "with",
}
STYLE_SELECTION_NEGATIONS = {"avoid", "never", "no", "not", "without"}
STYLE_SELECTION_CONTEXT_TOKENS = 5


class StyleProviderError(ValueError):
    """The installed provider or immutable selection evidence is unusable."""


def _selection_words(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = normalized.replace("don’t", "do not").replace("don't", "do not")
    normalized = normalized.replace("dont", "do not")
    return re.findall(r"[a-z0-9]+", normalized)


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source_evidence(path: pathlib.Path, kind: str, markers: dict) -> dict:
    raw = path.read_bytes()
    return {
        "kind": kind,
        "path": str(path),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "derivation_schema": STYLE_DERIVATION_SCHEMA,
        "marker_set_sha256": _canonical_sha256(markers),
    }


def _normalized_colors(values: object, *, key: str, source: pathlib.Path) -> list[str]:
    if not isinstance(values, list) or not values:
        raise StyleProviderError(f"cannot verify report style: {source} needs a non-empty {key} list")
    if not all(isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", value) for value in values):
        raise StyleProviderError(f"cannot verify report style: {source} has an invalid color in {key}")
    normalized = list(dict.fromkeys(value.upper() for value in values))
    if any(value in NON_EVIDENT_COLORS for value in normalized):
        raise StyleProviderError(
            f"cannot verify report style: {key} in {source} contains ubiquitous white or black"
        )
    return normalized


def _validate_markers(markers: object, source: pathlib.Path) -> dict:
    if not isinstance(markers, dict):
        raise StyleProviderError(f"cannot verify report style: {source} has no pdf_markers object")
    required = _normalized_colors(markers.get("required_any"), key="required_any", source=source)
    supporting = [
        value for value in _normalized_colors(
            markers.get("supporting_any"), key="supporting_any", source=source
        ) if value not in required
    ]
    minimum = markers.get("minimum_distinct_markers")
    if (
        not isinstance(minimum, int)
        or isinstance(minimum, bool)
        or minimum < 2
        or minimum > len(set(required + supporting))
    ):
        raise StyleProviderError(
            f"cannot verify report style: {source} needs a satisfiable minimum_distinct_markers >= 2"
        )
    if not supporting:
        raise StyleProviderError(
            f"cannot verify report style: {source} needs an independent supporting marker"
        )
    return {
        "required_any": required,
        "supporting_any": supporting,
        "minimum_distinct_markers": minimum,
    }


def _validated_aliases(aliases: object, source: pathlib.Path) -> list[str]:
    if (
        not isinstance(aliases, list)
        or not aliases
        or not all(isinstance(alias, str) and alias.strip() for alias in aliases)
    ):
        raise StyleProviderError(
            f"cannot verify report style: explicit-only provider {source} needs "
            "non-empty user_selection_aliases"
        )
    normalized = [_selection_words(alias) for alias in aliases]
    if any(not words or not STYLE_SELECTION_NOUNS.intersection(words) for words in normalized):
        raise StyleProviderError(
            f"cannot verify report style: every user selection alias in {source} must name a "
            "style, skill, template, or brand"
        )
    if len({tuple(words) for words in normalized}) != len(normalized):
        raise StyleProviderError(f"cannot verify report style: {source} has duplicate selection aliases")
    return [alias.strip() for alias in aliases]


def _profile_provider(path: pathlib.Path, expected_provider: str) -> tuple[dict, dict]:
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StyleProviderError(f"cannot verify report style: unreadable profile at {path}") from exc
    if not isinstance(profile, dict) or profile.get("schema") != REPORT_STYLE_SCHEMA:
        raise StyleProviderError(f"cannot verify report style: {path} is not {REPORT_STYLE_SCHEMA!r}")
    provider = profile.get("provider")
    if provider != expected_provider:
        raise StyleProviderError(
            f"cannot verify report style: requested provider {expected_provider!r} but {path} "
            f"declares {provider!r}"
        )
    activation = profile.get("activation")
    if activation not in {"default", "explicit_only"}:
        raise StyleProviderError(f"cannot verify report style: {path} has an invalid activation policy")
    aliases = profile.get("user_selection_aliases")
    if activation == "explicit_only":
        aliases = _validated_aliases(aliases, path)
    elif aliases is not None:
        raise StyleProviderError(
            f"cannot verify report style: default provider {path} must not declare explicit-selection aliases"
        )
    markers = _validate_markers(profile.get("pdf_markers"), path)
    normalized = {**profile, "pdf_markers": markers}
    if aliases is not None:
        normalized["user_selection_aliases"] = aliases
    return normalized, _source_evidence(path, "provider_profile", markers)


def _frontmatter_name(text: str, source: pathlib.Path) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise StyleProviderError(f"cannot verify report style: {source} has no YAML frontmatter")
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration as exc:
        raise StyleProviderError(f"cannot verify report style: {source} has unterminated frontmatter") from exc
    match = FRONTMATTER_NAME_RE.search("\n".join(lines[1:end]))
    if not match:
        raise StyleProviderError(f"cannot verify report style: {source} has no frontmatter name")
    return match.group(1).strip().strip("'\"")


def _brand_section(text: str, source: pathlib.Path) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if not match:
            continue
        title = " ".join(_selection_words(match.group(2)))
        if not (
            "brand at a glance" in title
            or "color palette" in title
            or "colour palette" in title
            or title in {"brand palette", "brand tokens", "palette"}
        ):
            continue
        level = len(match.group(1))
        end = len(lines)
        for cursor in range(index + 1, len(lines)):
            next_heading = HEADING_RE.match(lines[cursor])
            if next_heading and len(next_heading.group(1)) <= level:
                end = cursor
                break
        return "\n".join(lines[index + 1:end])
    raise StyleProviderError(
        f"cannot verify report style: {source} has no bounded brand palette section"
    )


def _line_colors(line: str) -> list[tuple[int, int, str]]:
    colors = []
    for match in HEX_COLOR_RE.finditer(line):
        token = match.group(0).strip("'\"")
        if not token.startswith("#"):
            token = "#" + token
        colors.append((match.start(), match.end(), token.upper()))
    return colors


def _derived_markers(text: str, source: pathlib.Path) -> dict:
    section = _brand_section(text, source)
    colors: list[str] = []
    primary: set[str] = set()
    for line in section.splitlines():
        found = _line_colors(line)
        colors.extend(color for _, _, color in found if color not in colors)
        for keyword in PRIMARY_MARKER_RE.finditer(line):
            before = [item for item in found if item[1] <= keyword.start()]
            after = [item for item in found if item[0] >= keyword.end()]
            nearest = before[-1] if before else (after[0] if after else None)
            if nearest is not None:
                primary.add(nearest[2])
    primary.difference_update(NON_EVIDENT_COLORS)
    if len(primary) != 1:
        raise StyleProviderError(
            f"cannot verify report style: {source} must identify exactly one primary accent or logo "
            f"color in its bounded brand palette section (found {len(primary)})"
        )
    required = next(iter(primary))
    supporting = [color for color in colors if color not in NON_EVIDENT_COLORS | {required}]
    return _validate_markers(
        {
            "required_any": [required],
            "supporting_any": supporting,
            "minimum_distinct_markers": 2,
        },
        source,
    )


def _derived_aliases(provider: str) -> list[str]:
    if not provider.endswith("-styling"):
        raise StyleProviderError(
            f"cannot discover legacy explicit provider {provider!r}: its slug must end in '-styling'"
        )
    base = provider.removesuffix("-styling").replace("-", " ")
    return [
        f"{base} styling",
        f"{base} style",
        f"{base} house style",
        f"{base} skill",
        f"{base} template",
    ]


def _markdown_provider(
    path: pathlib.Path,
    expected_provider: str,
    activation: str,
) -> tuple[dict, dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise StyleProviderError(f"cannot verify report style: unreadable installed skill at {path}") from exc
    declared = _frontmatter_name(text, path)
    if declared != expected_provider:
        raise StyleProviderError(
            f"cannot verify report style: requested provider {expected_provider!r} but {path} "
            f"declares {declared!r}"
        )
    markers = _derived_markers(text, path)
    profile = {
        "schema": REPORT_STYLE_SCHEMA,
        "provider": expected_provider,
        "activation": activation,
        "pdf_markers": markers,
    }
    if activation == "explicit_only":
        profile["user_selection_aliases"] = _derived_aliases(expected_provider)
    return profile, _source_evidence(path, "installed_skill_markdown", markers)


def _installed_provider_dir(provider: str, roots: tuple[pathlib.Path, ...]) -> pathlib.Path:
    if not isinstance(provider, str) or not STYLE_PROVIDER_RE.fullmatch(provider):
        raise StyleProviderError("report style provider must be a lowercase skill slug")
    checked = []
    for root in roots:
        candidate = root / provider
        checked.append(str(candidate))
        try:
            resolved_root = root.resolve(strict=True)
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(resolved_root)
        except (FileNotFoundError, ValueError):
            continue
        if resolved.is_dir():
            return resolved
    raise StyleProviderError(
        f"cannot verify report style for provider {provider!r}: no provider-owned "
        f"{STYLE_PROFILE_NAME} or {STYLE_SKILL_NAME} at any configured skills root "
        f"({', '.join(checked)})"
    )


def resolve_provider(
    provider: str,
    roots: tuple[pathlib.Path, ...],
    *,
    activation_hint: str | None = None,
) -> tuple[dict, pathlib.Path, dict]:
    """Resolve a provider profile, preferring structured data and failing closed on bad data."""
    directory = _installed_provider_dir(provider, roots)
    return validate_provider_directory(directory, activation_hint=activation_hint)


def validate_provider_directory(
    directory: pathlib.Path,
    *,
    activation_hint: str | None = None,
) -> tuple[dict, pathlib.Path, dict]:
    """Validate one provider directory for authoring or after fixed-root resolution."""
    provider = directory.name
    if not STYLE_PROVIDER_RE.fullmatch(provider):
        raise StyleProviderError("report style provider directory must be a lowercase skill slug")
    skill_path = directory / STYLE_SKILL_NAME
    if not skill_path.is_file():
        raise StyleProviderError(
            f"cannot verify report style for provider {provider!r}: installed provider has no "
            f"{STYLE_SKILL_NAME}"
        )
    try:
        declared = _frontmatter_name(skill_path.read_text(encoding="utf-8"), skill_path)
    except (OSError, UnicodeError) as exc:
        raise StyleProviderError(f"cannot verify report style: unreadable installed skill at {skill_path}") from exc
    if declared != provider:
        raise StyleProviderError(
            f"cannot verify report style: requested provider {provider!r} but {skill_path} "
            f"declares {declared!r}"
        )
    profile_path = directory / "assets" / STYLE_PROFILE_NAME
    if profile_path.is_file():
        profile, source = _profile_provider(profile_path, provider)
        if activation_hint is not None and profile.get("activation") != activation_hint:
            raise StyleProviderError(
                f"cannot verify report style: provider {provider!r} declares activation "
                f"{profile.get('activation')!r}, expected {activation_hint!r}"
            )
        return profile, profile_path, source
    activation = activation_hint or ("explicit_only" if provider.endswith("-styling") else "default")
    if activation not in {"default", "explicit_only"}:
        raise StyleProviderError(f"cannot verify report style: invalid activation hint {activation!r}")
    profile, source = _markdown_provider(skill_path, provider, activation)
    return profile, skill_path, source


def _safe_profile_aliases(path: pathlib.Path) -> list[str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    aliases = raw.get("user_selection_aliases") if isinstance(raw, dict) else None
    if not isinstance(aliases, list):
        return []
    return [
        alias.strip() for alias in aliases
        if isinstance(alias, str)
        and alias.strip()
        and STYLE_SELECTION_NOUNS.intersection(_selection_words(alias))
    ]


def discover_explicit_providers(
    roots: tuple[pathlib.Path, ...],
) -> tuple[dict[str, dict], dict[str, str]]:
    """Discover compatible providers without a customer registry or caller-supplied path."""
    providers: dict[str, dict] = {}
    invalid: dict[str, str] = {}
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        try:
            resolved_root = root.resolve(strict=True)
        except FileNotFoundError:
            continue
        for entry in sorted(root.iterdir(), key=lambda path: path.name):
            provider = entry.name
            if provider in seen or not STYLE_PROVIDER_RE.fullmatch(provider):
                continue
            try:
                directory = entry.resolve(strict=True)
                directory.relative_to(resolved_root)
            except (FileNotFoundError, ValueError):
                continue
            if not directory.is_dir():
                continue
            profile_path = directory / "assets" / STYLE_PROFILE_NAME
            skill_path = directory / STYLE_SKILL_NAME
            if profile_path.is_file():
                aliases = _safe_profile_aliases(profile_path)
            elif provider.endswith("-styling") and skill_path.is_file():
                aliases = _derived_aliases(provider)
            else:
                continue
            seen.add(provider)
            if aliases:
                providers[provider] = {"user_selection_aliases": aliases}
            try:
                profile, _, _ = resolve_provider(
                    provider,
                    roots,
                    activation_hint="explicit_only",
                )
                if profile.get("activation") != "explicit_only":
                    raise StyleProviderError("selected report style provider is not explicit-only")
                providers[provider] = profile
            except StyleProviderError as exc:
                invalid[provider] = str(exc)
    return providers, invalid


def _serialized_message_records(path: pathlib.Path) -> tuple[bytes, list[dict]]:
    raw = path.read_bytes()
    try:
        payloads = [json.loads(raw)]
    except (ValueError, UnicodeError):
        payloads = []
        for line_number, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            try:
                payloads.append(json.loads(line))
            except (ValueError, UnicodeError) as exc:
                raise StyleProviderError(
                    f"execution transcript has malformed JSON at record {line_number}"
                ) from exc
    if not payloads:
        raise StyleProviderError("execution transcript is empty")
    records: list[dict] = []

    def visit(value: object, *, record_index: int | None = None) -> None:
        if isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, record_index=index)
            return
        if not isinstance(value, dict):
            return
        role = value.get("role") or value.get("type")
        if role in {"user", "assistant"} and "content" in value:
            record = dict(value)
            if record_index is not None:
                record["_transcript_record_index"] = record_index
            records.append(record)
            return
        for key in ("data", "messages", "items"):
            nested = value.get(key)
            if isinstance(nested, (list, dict)):
                visit(nested, record_index=0 if isinstance(nested, dict) else None)

    for payload in payloads:
        visit(payload)
    return raw, records


def _message_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(part for item in value if (part := _message_text(item)))
    if not isinstance(value, dict):
        return ""
    kind = value.get("type")
    text = value.get("text")
    if kind in {"text", "input_text", "output_text"} and isinstance(text, str):
        return text
    content = value.get("content")
    return _message_text(content) if isinstance(content, (str, list, dict)) else ""


def _user_messages(path: pathlib.Path) -> tuple[bytes, list[dict]]:
    raw, records = _serialized_message_records(path)
    grouped: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for record in records:
        if (record.get("role") or record.get("type")) != "user":
            continue
        message_id = record.get("id") or record.get("message_id")
        message_index = record.get("i")
        if isinstance(message_id, (str, int)) and str(message_id).strip():
            locator = ("id", str(message_id))
        elif isinstance(message_index, (str, int)) and str(message_index).strip():
            locator = ("index", str(message_index))
        elif isinstance(record.get("_transcript_record_index"), int):
            locator = ("index", str(record["_transcript_record_index"]))
        else:
            raise StyleProviderError("user message in execution transcript has no immutable id or index")
        content = _message_text(record.get("content"))
        if not content:
            continue
        if locator not in grouped:
            grouped[locator] = {"locator": locator, "chunks": []}
            order.append(locator)
        grouped[locator]["chunks"].append(content)
    messages = []
    for locator in order:
        text = "".join(grouped[locator]["chunks"])
        messages.append({
            "locator": locator,
            "text": text,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        })
    return raw, messages


def _alias_occurrences(words: list[str], alias_words: list[str]) -> list[int]:
    width = len(alias_words)
    return [
        index for index in range(len(words) - width + 1)
        if words[index:index + width] == alias_words
    ]


def _negative_style_directive(words: list[str], index: int, width: int) -> bool:
    before = words[max(0, index - STYLE_SELECTION_CONTEXT_TOKENS):index]
    after = words[index + width:index + width + STYLE_SELECTION_CONTEXT_TOKENS]
    if before[-1:] and before[-1] in STYLE_SELECTION_NEGATIONS:
        return True
    if before[-2:] in (["instead", "of"], ["rather", "than"]):
        return True
    action_positions = [
        position for position, word in enumerate(before)
        if word in STYLE_SELECTION_ACTIONS or word == "avoid"
    ]
    if action_positions:
        action = action_positions[-1]
        if before[action] == "avoid" or STYLE_SELECTION_NEGATIONS.intersection(
            before[max(0, action - 2):action]
        ):
            return True
    for action, word in enumerate(after):
        if word in STYLE_SELECTION_ACTIONS and STYLE_SELECTION_NEGATIONS.intersection(after[:action]):
            return True
    return False


def _message_style_directives(text: str, providers: dict[str, dict]) -> tuple[set[str], set[str], dict[str, str]]:
    words = _selection_words(text)
    positive: set[str] = set()
    negative: set[str] = set()
    matched_alias: dict[str, str] = {}
    for provider, profile in providers.items():
        for alias in profile["user_selection_aliases"]:
            alias_words = _selection_words(alias)
            for index in _alias_occurrences(words, alias_words):
                context = (
                    words[max(0, index - STYLE_SELECTION_CONTEXT_TOKENS):index]
                    + words[index + len(alias_words):index + len(alias_words) + STYLE_SELECTION_CONTEXT_TOKENS]
                )
                if _negative_style_directive(words, index, len(alias_words)):
                    negative.add(provider)
                    matched_alias[provider] = alias
                elif STYLE_SELECTION_ACTIONS.intersection(context):
                    positive.add(provider)
                    matched_alias[provider] = alias
    return positive, negative, matched_alias


def selected_style_from_transcript(
    transcript: pathlib.Path,
    roots: tuple[pathlib.Path, ...],
    transcript_relative_path: str,
) -> tuple[str | None, dict | None]:
    """Derive the current explicit provider solely from immutable user messages."""
    raw, messages = _user_messages(transcript)
    providers, invalid = discover_explicit_providers(roots)
    selected_provider: str | None = None
    selected_evidence: dict | None = None
    for message in messages:
        positive, negative, aliases = _message_style_directives(message["text"], providers)
        if positive.intersection(negative) or len(positive) > 1:
            raise StyleProviderError("user messages contain a conflicting report-style selection")
        if selected_provider in negative:
            selected_provider = None
            selected_evidence = None
        if positive:
            next_provider = next(iter(positive))
            if next_provider in invalid:
                raise StyleProviderError(
                    f"selected report style provider {next_provider!r} is unavailable: {invalid[next_provider]}"
                )
            if selected_provider is not None and selected_provider != next_provider:
                raise StyleProviderError("user messages contain unresolved competing report-style selections")
            selected_provider = next_provider
            locator_kind, locator_value = message["locator"]
            selected_evidence = {
                "source": "user_message",
                "transcript_path": transcript_relative_path,
                "transcript_sha256": hashlib.sha256(raw).hexdigest(),
                "message_locator": {"kind": locator_kind, "value": locator_value},
                "message_sha256": message["sha256"],
                "matched_alias": aliases[selected_provider],
            }
    return selected_provider, selected_evidence
