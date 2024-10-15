from pydantic import BaseModel
from pydantic import __version__ as pydantic_version


HAS_PYDANTIC2 = pydantic_version.startswith("2.")


def get_model_dump(obj: BaseModel, **kwargs) -> dict:
    """Helper method to get the model dump of a pydnatic model.

    This should work with both pydantic 1 and pydantic 2.

    Args:
        obj (BaseModel): The base model.
        kwargs: The keyword aguments that will be passed on.

    Returns:
        dict: The model dump.
    """
    # NOTE: The type ingores are based on pydantic 2
    if HAS_PYDANTIC2:
        return obj.model_dump(**kwargs)  # type: ignore
    # for before pydantic 2
    return obj.dict(**kwargs)  # type: ignore # 4pydantic1 - backwards compatibility


def get_model_fields(obj: BaseModel) -> dict:
    if HAS_PYDANTIC2:
        return obj.model_fields  # type: ignore
    return obj.__fields__  # type: ignore # 4pydantic1 - backwards compatibility