"""
LSTM Model for Price Prediction
Predicts next 1-hour price movement based on 15-hour historical data.
"""

import logging
import torch
import torch.nn as nn
import numpy as np
from pathlib import Path
from typing import Optional, Tuple
import sys

logger = logging.getLogger(__name__)


class LSTMPricePredictor(nn.Module):
    """
    LSTM neural network for predicting cryptocurrency price movements.
    
    Input: (batch_size, lookback_periods, num_features)
    Output: (batch_size, prediction_horizon)  - percentage change predictions
    """
    
    def __init__(self, input_size: int = 12, 
                 hidden_size: int = 128,
                 num_layers: int = 2,
                 dropout: float = 0.2,
                 output_size: int = 1):
        """
        Initialize LSTM model.
        
        Args:
            input_size: Number of input features (12)
            hidden_size: Number of LSTM units (128)
            num_layers: Number of LSTM layers (2)
            dropout: Dropout rate (0.2)
            output_size: Number of output predictions (1 for next period prediction)
        """
        super(LSTMPricePredictor, self).__init__()
        
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.output_size = output_size
        
        # LSTM layers
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            dropout=dropout if num_layers > 1 else 0,
            batch_first=True
        )
        
        # Attention-like output layer
        self.fc_hidden = nn.Linear(hidden_size, 64)
        self.relu = nn.ReLU()
        self.dropout = nn.Dropout(dropout)
        self.fc_output = nn.Linear(64, output_size)
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Forward pass.
        
        Args:
            x: Input tensor (batch_size, lookback_periods, input_size)
        
        Returns:
            Output tensor (batch_size, output_size) with predicted price changes
        """
        # LSTM expects (batch, seq_len, features)
        lstm_out, (hidden, cell) = self.lstm(x)
        
        # Use last output
        last_hidden = hidden[-1]  # (batch_size, hidden_size)
        
        # Fully connected layers
        hidden_out = self.fc_hidden(last_hidden)
        hidden_out = self.relu(hidden_out)
        hidden_out = self.dropout(hidden_out)
        
        # Output: price change predictions
        output = self.fc_output(hidden_out)
        
        return output  # (batch_size, output_size)
    
    def predict(self, x: np.ndarray) -> np.ndarray:
        """
        Make predictions on numpy array (evaluation mode).
        
        Args:
            x: Input array (lookback_periods, num_features)
        
        Returns:
            Predicted price changes as array
        """
        self.eval()
        with torch.no_grad():
            # Add batch dimension
            x_tensor = torch.FloatTensor(x).unsqueeze(0)
            output = self.forward(x_tensor)
            return output.squeeze(0).cpu().numpy()


class ModelManager:
    """
    Manages model loading, saving, and inference.
    Supports hot-reload when model file is updated externally (e.g., by auto-retrain).
    """
    
    def __init__(self, model_dir: Optional[str] = None):
        """
        Initialize model manager.
        
        Args:
            model_dir: Directory to store model files
        """
        self.model_dir = Path(model_dir) if model_dir else Path(__file__).parent.parent.parent / "models"
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model: Optional[LSTMPricePredictor] = None
        self._loaded_model_mtime: Optional[float] = None  # Track file modification time
    
    def create_model(self, input_size: int = 12,
                    hidden_size: int = 128,
                    num_layers: int = 2,
                    dropout: float = 0.2,
                    output_size: int = 4) -> LSTMPricePredictor:
        """Create a new model"""
        self.model = LSTMPricePredictor(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            dropout=dropout,
            output_size=output_size
        ).to(self.device)
        
        logger.info(f"Created new LSTM model on device: {self.device}")
        return self.model
    
    def save_model(self, model_name: str = "lstm_model") -> Path:
        """
        Save model to disk.
        
        Args:
            model_name: Name of model file (without extension)
        
        Returns:
            Path to saved model
        """
        if self.model is None:
            logger.error("No model to save")
            return None
        
        model_path = self.model_dir / f"{model_name}.pt"
        torch.save({
            'model_state_dict': self.model.state_dict(),
            'model_config': {
                'input_size': self.model.input_size,
                'hidden_size': self.model.hidden_size,
                'num_layers': self.model.num_layers,
                'output_size': self.model.output_size,
            }
        }, model_path)
        
        logger.info(f"Model saved to {model_path}")
        return model_path
    
    def load_model(self, model_name: str = "lstm_model") -> Optional[LSTMPricePredictor]:
        """
        Load model from disk.
        
        Args:
            model_name: Name of model file (without extension)
        
        Returns:
            Loaded model or None if not found
        """
        model_path = self.model_dir / f"{model_name}.pt"
        
        if not model_path.exists():
            logger.warning(f"Model not found at {model_path}")
            return None
        
        checkpoint = torch.load(model_path, map_location=self.device)
        config = checkpoint['model_config']
        
        # Recreate model
        self.model = LSTMPricePredictor(
            input_size=config['input_size'],
            hidden_size=config['hidden_size'],
            num_layers=config['num_layers'],
            output_size=config['output_size']
        ).to(self.device)
        
        self.model.load_state_dict(checkpoint['model_state_dict'])
        self.model.eval()
        
        # Track file modification time for hot-reload
        self._loaded_model_mtime = model_path.stat().st_mtime
        
        logger.info(f"Loaded model from {model_path}")
        return self.model
    
    def get_model_age_hours(self, model_name: str = "lstm_model") -> Optional[float]:
        """
        Get age of model file in hours since last modification.
        
        Returns:
            Age in hours, or None if model file doesn't exist
        """
        import time
        model_path = self.model_dir / f"{model_name}.pt"
        if not model_path.exists():
            return None
        mtime = model_path.stat().st_mtime
        age_seconds = time.time() - mtime
        return age_seconds / 3600.0
    
    def reload_if_changed(self, model_name: str = "lstm_model") -> bool:
        """
        Check if model file has been updated since last load, and reload if so.
        Used for hot-reloading after auto-retrain completes.
        
        Returns:
            True if model was reloaded, False otherwise
        """
        model_path = self.model_dir / f"{model_name}.pt"
        if not model_path.exists():
            return False
        
        current_mtime = model_path.stat().st_mtime
        if self._loaded_model_mtime is not None and current_mtime > self._loaded_model_mtime:
            logger.info(f"🔄 Model file updated on disk — hot-reloading...")
            result = self.load_model(model_name)
            if result is not None:
                logger.info(f"✅ Model hot-reloaded successfully")
                return True
            else:
                logger.error("❌ Failed to hot-reload model")
        return False
    
    def predict(self, features: np.ndarray) -> Tuple[np.ndarray, float]:
        """
        Make prediction on features.
        
        Args:
            features: (lookback_periods, num_features) array
        
        Returns:
            (predictions, confidence_score)
            predictions: predicted price change (as decimal, e.g., 0.015 = +1.5%)
            confidence_score: confidence metric [0-1] based on prediction magnitude
                             Small predictions (~0) = low confidence (model unsure)
                             Moderate predictions = higher confidence
                             Extreme predictions (>10%) = capped confidence (likely noise)
        """
        if self.model is None:
            logger.error("No model loaded")
            return None, 0.0
        
        if features.shape[0] == 0:
            logger.warning("Empty features")
            return None, 0.0
        
        self.model.eval()
        with torch.no_grad():
            x = torch.FloatTensor(features).unsqueeze(0).to(self.device)
            output = self.model(x)
            
            # Work with tensors directly
            predictions_tensor = output.squeeze(0).cpu()
            avg_pred = torch.mean(predictions_tensor).item()
            abs_pred = abs(avg_pred)
            
            # Confidence: bell-shaped curve that rewards moderate predictions
            # - Very small moves (<0.1%): low confidence (noise / no signal)
            # - Moderate moves (0.5%-3%): high confidence (realistic predictions)
            # - Extreme moves (>5%): decreasing confidence (likely overfitting/noise)
            # Formula: confidence rises with abs_pred, then decays for extremes
            if abs_pred < 0.001:    # < 0.1%
                confidence = abs_pred / 0.001 * 0.3  # 0.0 - 0.3
            elif abs_pred < 0.03:   # 0.1% - 3%
                confidence = 0.3 + (abs_pred - 0.001) / 0.029 * 0.7  # 0.3 - 1.0
            elif abs_pred < 0.10:   # 3% - 10%
                confidence = 1.0 - (abs_pred - 0.03) / 0.07 * 0.5  # 1.0 - 0.5
            else:                   # > 10%
                confidence = max(0.1, 0.5 - (abs_pred - 0.10) * 2)  # decays to 0.1
            
            confidence = max(0.0, min(1.0, confidence))
            predictions = avg_pred
        
        return predictions, confidence
    
    def predict_price_move_1h(self, features) -> Tuple[float, float]:
        """
        Predict price movement over next 1 hour.
        
        Args:
            features: (lookback_periods, num_features) array
        
        Returns:
            (predicted_move_pct, confidence)
            predicted_move_pct: expected price change in % (e.g., 0.015 for +1.5%)
        """
        predictions, confidence = self.predict(features)
        
        if predictions is None:
            return 0.0, 0.0
        
        return float(predictions), confidence
    
    def model_summary(self) -> str:
        """Return string summary of model architecture"""
        if self.model is None:
            return "No model loaded"
        
        summary = f"""
LSTM Price Predictor Model
==========================
Input Size: {self.model.input_size}
Hidden Size: {self.model.hidden_size}
Num Layers: {self.model.num_layers}
Output Size: {self.model.output_size}
Device: {self.device}

Total Parameters: {sum(p.numel() for p in self.model.parameters()):,}
Trainable: {sum(p.numel() for p in self.model.parameters() if p.requires_grad):,}
"""
        return summary
