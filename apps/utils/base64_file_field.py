import filetype
from drf_extra_fields.fields import Base64FileField


class CustomBase64FileField(Base64FileField):
    """
    A custom serializer field to handle base64-encoded files.
    """
    ALLOWED_MIME_TYPES = {
        'application/pdf': 'pdf',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
    }

    ALLOWED_TYPES = ['pdf', 'docx']

    def get_file_extension(self, filename, decoded_file):
        extension = filetype.guess_extension(decoded_file)
        return extension

    def to_internal_value(self, data):
        if isinstance(data, str):
            return super().to_internal_value(data)
        return data
