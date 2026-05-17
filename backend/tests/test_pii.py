from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.pii import create_redactor
from src.pii.fallback import RegexRedactor
from src.pii.redactor import PIIRedactor


class TestRegexRedactor:
    def setup_method(self) -> None:
        self.redactor = RegexRedactor()

    def test_redacts_ssn(self) -> None:
        text = "Client SSN is 123-45-6789 on file."
        result = self.redactor.redact(text)
        assert "123-45-6789" not in result.redacted_text
        assert "[SSN_REDACTED]" in result.redacted_text
        assert any(f.info_type == "SSN" for f in result.findings)

    def test_redacts_phone_with_dashes(self) -> None:
        text = "Call the client at 555-123-4567."
        result = self.redactor.redact(text)
        assert "555-123-4567" not in result.redacted_text
        assert "[PHONE_REDACTED]" in result.redacted_text

    def test_redacts_phone_with_parens(self) -> None:
        text = "Contact number: (555) 123-4567."
        result = self.redactor.redact(text)
        assert "(555) 123-4567" not in result.redacted_text
        assert "[PHONE_REDACTED]" in result.redacted_text

    def test_redacts_email(self) -> None:
        text = "Send to john.smith@meridianwealth.com for review."
        result = self.redactor.redact(text)
        assert "john.smith@meridianwealth.com" not in result.redacted_text
        assert "[EMAIL_REDACTED]" in result.redacted_text
        assert any(f.info_type == "EMAIL" for f in result.findings)

    def test_redacts_account_number(self) -> None:
        text = "Account #4821-7734 balance update."
        result = self.redactor.redact(text)
        assert "4821-7734" not in result.redacted_text
        assert "[ACCOUNT_REDACTED]" in result.redacted_text

    def test_redacts_multiple_pii_types(self) -> None:
        text = (
            "Client John (SSN: 987-65-4321) reachable at john@example.com "
            "or 555-999-0001. Account: 1234-5678."
        )
        result = self.redactor.redact(text)
        assert "987-65-4321" not in result.redacted_text
        assert "john@example.com" not in result.redacted_text
        assert "555-999-0001" not in result.redacted_text
        assert "1234-5678" not in result.redacted_text
        assert len(result.findings) >= 4

    def test_no_pii_returns_unchanged(self) -> None:
        text = "The fund returned 8.5% in Q3 with moderate volatility."
        result = self.redactor.redact(text)
        assert result.redacted_text == text
        assert result.findings == []


class TestPIIRedactorMocked:
    @patch("src.pii.redactor.dlp_v2.DlpServiceClient")
    def test_dlp_redactor_calls_deidentify(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        mock_response = MagicMock()
        mock_response.item.value = "[PERSON_NAME] account [FINANCIAL_ACCOUNT_NUMBER] shows..."
        mock_summary = MagicMock()
        mock_summary.info_type.name = "PERSON_NAME"
        mock_response.overview.transformation_summaries = [mock_summary]
        mock_client.deidentify_content.return_value = mock_response

        redactor = PIIRedactor(project_id="test-project")
        result = redactor.redact("John Smith account 482177 shows...")

        mock_client.deidentify_content.assert_called_once()
        assert result.redacted_text == "[PERSON_NAME] account [FINANCIAL_ACCOUNT_NUMBER] shows..."
        assert len(result.findings) == 1
        assert result.findings[0].info_type == "PERSON_NAME"


class TestFactory:
    def test_returns_regex_by_default(self) -> None:
        settings = MagicMock()
        settings.pii_mode = "regex"
        settings.gcp_project_id = ""
        redactor = create_redactor(settings)
        assert isinstance(redactor, RegexRedactor)

    @patch("src.pii.redactor.dlp_v2.DlpServiceClient")
    def test_returns_dlp_when_configured(self, mock_client_cls: MagicMock) -> None:
        settings = MagicMock()
        settings.pii_mode = "dlp"
        settings.gcp_project_id = "my-project"
        redactor = create_redactor(settings)
        assert isinstance(redactor, PIIRedactor)
