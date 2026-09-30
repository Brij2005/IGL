"""
Abstract Base Class for all AI Perception and Diagnostics Models.
Provides standard model lifecycle: load(), predict(), health(), version().
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, List
import numpy as np


class AIModel(ABC):
    """Abstract Base Class enforcing a uniform interface across all AI modules."""

    @abstractmethod
    def load(self, weights_path: str) -> bool:
        """Load model weights into memory."""
        pass

    @abstractmethod
    def predict(self, frame: np.ndarray, **kwargs) -> List[Dict[str, Any]]:
        """
        Run inference on a single frame.
        Returns a list of detection dicts:
        [{'class_name': str, 'confidence': float, 'bbox': [x1, y1, x2, y2]}, ...]
        """
        pass

    @abstractmethod
    def health(self) -> Dict[str, Any]:
        """Return model operational health and performance stats."""
        pass

    @abstractmethod
    def version(self) -> str:
        """Return model version string."""
        pass
