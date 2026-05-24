"""The Model seam is a one-method protocol; the rest of jiti depends only on that."""

from jiti.model import Model


def test_a_custom_model_satisfies_the_protocol():
    class EchoModel:
        def complete(self, prompt: str) -> str:
            return prompt.upper()

    model = EchoModel()

    assert isinstance(model, Model)
    assert model.complete("hi") == "HI"
