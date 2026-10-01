"""
Image Preprocessing Service using OpenCV and Pillow.
"""

import io
from typing import Optional, Tuple
import cv2
import numpy as np
from PIL import Image


class ImageService:
    """Handles image decoding, orientation correction, and non-destructive preprocessing."""

    @staticmethod
    def load_image_from_bytes(image_bytes: bytes) -> np.ndarray:
        """
        Loads image bytes into a numpy array (BGR).
        """
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            # Fallback to Pillow if OpenCV fails on some WEBP / TIFF formats
            pil_img = Image.open(io.BytesIO(image_bytes))
            if pil_img.mode != "RGB":
                pil_img = pil_img.convert("RGB")
            img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        return img

    @classmethod
    def preprocess_image(
        cls,
        image: np.ndarray,
        enhance_contrast: bool = True,
        denoise: bool = False,
        auto_deskew: bool = True,
        as_3channel: bool = True,
    ) -> np.ndarray:
        """
        Preprocesses image for OCR while carefully preserving faint characters.
        Returns a 3-channel BGR image by default for stable PaddleOCR processing.
        """
        # Convert to Grayscale if 3-channel
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image.copy()

        # Deskew if requested and angle detected
        if auto_deskew:
            gray = cls._deskew(gray)

        # Contrast enhancement using CLAHE to avoid destroying faint characters
        if enhance_contrast:
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            gray = clahe.apply(gray)

        # Denoising only if noisy
        if denoise:
            gray = cv2.fastNlMeansDenoising(gray, h=10, templateWindowSize=7, searchWindowSize=21)

        if as_3channel:
            return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

        return gray

    @staticmethod
    def _deskew(image: np.ndarray) -> np.ndarray:
        """
        Detects skew angle and rotates the image back if tilted significantly.
        """
        try:
            # Detect edges
            edges = cv2.Canny(image, 50, 150, apertureSize=3)
            lines = cv2.HoughLinesP(edges, 1, np.pi / 180, 100, minLineLength=100, maxLineGap=10)
            
            if lines is not None and len(lines) > 0:
                angles = []
                for line in lines:
                    x1, y1, x2, y2 = line[0]
                    angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
                    if abs(angle) < 45:  # Consider only near-horizontal lines
                        angles.append(angle)
                
                if angles:
                    median_angle = float(np.median(angles))
                    if abs(median_angle) > 0.5:  # Only deskew if tilt > 0.5 degrees
                        (h, w) = image.shape[:2]
                        center = (w // 2, h // 2)
                        matrix = cv2.getRotationMatrix2D(center, median_angle, 1.0)
                        rotated = cv2.warpAffine(
                            image, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
                        )
                        return rotated
        except Exception:
            pass  # Fallback gracefully to unrotated image

        return image

    @staticmethod
    def image_to_bytes(image: np.ndarray, format_ext: str = ".png") -> bytes:
        """
        Encodes a numpy image back into bytes.
        """
        success, encoded = cv2.imencode(format_ext, image)
        if not success:
            raise ValueError(f"Failed to encode image to {format_ext}")
        return encoded.tobytes()
