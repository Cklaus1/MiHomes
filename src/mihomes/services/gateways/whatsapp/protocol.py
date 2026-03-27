"""WhatsApp bridge protocol — defines the interface for WhatsApp messaging integration."""

from typing import Protocol


class WhatsAppBridge(Protocol):
    """Protocol for WhatsApp messaging integration.

    Implementations should handle sending messages, receiving callbacks,
    and managing WhatsApp Business API connections.
    """

    def send_message(self, phone_number: str, message: str) -> dict:
        """Send a text message to a phone number.

        Args:
            phone_number: E.164 formatted phone number (e.g., +1234567890).
            message: Message text to send.

        Returns:
            Dict with message_id and delivery status.
        """
        ...

    def send_template(
        self,
        phone_number: str,
        template_name: str,
        parameters: dict | None = None,
    ) -> dict:
        """Send a pre-approved template message.

        Args:
            phone_number: E.164 formatted phone number.
            template_name: Name of the approved WhatsApp template.
            parameters: Template parameter values.

        Returns:
            Dict with message_id and delivery status.
        """
        ...

    def get_message_status(self, message_id: str) -> dict:
        """Check the delivery status of a sent message.

        Args:
            message_id: The message ID returned from send_message.

        Returns:
            Dict with status (sent, delivered, read, failed).
        """
        ...

    def register_webhook(self, callback_url: str) -> bool:
        """Register a webhook URL for incoming message callbacks.

        Args:
            callback_url: URL to receive webhook POST requests.

        Returns:
            True if registration was successful.
        """
        ...
