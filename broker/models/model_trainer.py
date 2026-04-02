"""
Model Training Module
Trains the LSTM model on historical data with walk-forward validation.
"""

import logging
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from typing import Tuple, Optional
from pathlib import Path
import json

from broker.models.lstm_model import LSTMPricePredictor

logger = logging.getLogger(__name__)


class ModelTrainer:
    """
    Trains the LSTM model on historical price data.
    Uses walk-forward validation to prevent data leakage.
    """
    
    def __init__(self, model: LSTMPricePredictor,
                 learning_rate: float = 0.001,
                 device: str = 'cpu'):
        """
        Initialize trainer.
        
        Args:
            model: LSTMPricePredictor instance
            learning_rate: Learning rate for optimizer
            device: 'cpu' or 'cuda'
        """
        self.model = model.to(device)
        self.device = torch.device(device)
        self.learning_rate = learning_rate
        self.optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
        self.criterion = nn.MSELoss()
        self.train_history = []
        self.val_history = []
    
    def prepare_sequences(self, data: np.ndarray, 
                         lookback: int = 60,
                         prediction_horizon: int = 4) -> Tuple[np.ndarray, np.ndarray]:
        """
        Prepare sequences for LSTM training.
        
        Args:
            data: (N, num_features) array of features
            lookback: Number of lookback periods (60 × 15min = 15h)
            prediction_horizon: Number of periods to predict (4 × 15min = 1h)
        
        Returns:
            (X, y) where X is reshaped input sequences and y is target price changes
        """
        if len(data) < lookback + prediction_horizon:
            logger.warning(f"Not enough data: {len(data)} < {lookback + prediction_horizon}")
            return np.array([]).reshape(0, lookback, data.shape[1]), np.array([])
        
        X = []
        y = []
        
        # Create rolling windows of size lookback
        for i in range(len(data) - lookback - prediction_horizon + 1):
            # Input window: lookback features
            window = data[i:i+lookback]  # Shape: (lookback, num_features)
            X.append(window)
            
            # Target: average return over next prediction_horizon periods
            # Use first feature (normalized close) as proxy price
            current_price = data[i+lookback-1, 0]
            future_price = data[i+lookback+prediction_horizon-1, 0]
            
            if current_price != 0:
                price_change = (future_price - current_price) / current_price
            else:
                price_change = 0.0
            
            y.append(price_change)
        
        return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)
    
    def train_epoch(self, train_loader: DataLoader) -> float:
        """
        Train for one epoch.
        
        Returns:
            Average loss for the epoch
        """
        self.model.train()
        total_loss = 0.0
        num_batches = 0
        
        for X_batch, y_batch in train_loader:
            X_batch = X_batch.to(self.device)
            y_batch = y_batch.to(self.device)
            
            # Forward pass
            predictions = self.model(X_batch)
            loss = self.criterion(predictions.squeeze(), y_batch)
            
            # Backward pass
            self.optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
            self.optimizer.step()
            
            total_loss += loss.item()
            num_batches += 1
        
        avg_loss = total_loss / num_batches if num_batches > 0 else 0
        return avg_loss
    
    def validate(self, val_loader: DataLoader) -> Tuple[float, float]:
        """
        Validate on validation set.
        
        Returns:
            (loss, mae) - Mean squared error and mean absolute error
        """
        self.model.eval()
        total_loss = 0.0
        total_mae = 0.0
        num_batches = 0
        
        with torch.no_grad():
            for X_batch, y_batch in val_loader:
                X_batch = X_batch.to(self.device)
                y_batch = y_batch.to(self.device)
                
                predictions = self.model(X_batch)
                loss = self.criterion(predictions.squeeze(), y_batch)
                mae = torch.mean(torch.abs(predictions.squeeze() - y_batch))
                
                total_loss += loss.item()
                total_mae += mae.item()
                num_batches += 1
        
        avg_loss = total_loss / num_batches if num_batches > 0 else 0
        avg_mae = total_mae / num_batches if num_batches > 0 else 0
        
        return avg_loss, avg_mae
    
    def train(self, X_train: np.ndarray, y_train: np.ndarray,
              X_val: np.ndarray, y_val: np.ndarray,
              epochs: int = 50,
              batch_size: int = 32,
              early_stopping_patience: int = 10) -> dict:
        """
        Train the model with early stopping.
        
        Args:
            X_train: Training features (N, lookback, num_features)
            y_train: Training targets (N,)
            X_val: Validation features
            y_val: Validation targets
            epochs: Number of epochs
            batch_size: Batch size
            early_stopping_patience: Epochs to wait before stopping
        
        Returns:
            Training history dict
        """
        # Create data loaders
        train_dataset = TensorDataset(
            torch.FloatTensor(X_train),
            torch.FloatTensor(y_train)
        )
        val_dataset = TensorDataset(
            torch.FloatTensor(X_val),
            torch.FloatTensor(y_val)
        )
        
        train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
        val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False)
        
        best_val_loss = float('inf')
        patience_counter = 0
        history = {'train_loss': [], 'val_loss': [], 'val_mae': [], 'best_epoch': 0}
        
        for epoch in range(epochs):
            train_loss = self.train_epoch(train_loader)
            val_loss, val_mae = self.validate(val_loader)
            
            history['train_loss'].append(train_loss)
            history['val_loss'].append(val_loss)
            history['val_mae'].append(val_mae)
            
            if (epoch + 1) % 10 == 0:
                logger.info(f"Epoch {epoch+1}/{epochs} - "
                          f"Train Loss: {train_loss:.6f}, "
                          f"Val Loss: {val_loss:.6f}, "
                          f"Val MAE: {val_mae:.6f}")
            
            # Early stopping
            if val_loss < best_val_loss:
                best_val_loss = val_loss
                patience_counter = 0
                history['best_epoch'] = epoch
            else:
                patience_counter += 1
                if patience_counter >= early_stopping_patience:
                    logger.info(f"Early stopping at epoch {epoch+1}")
                    break
        
        return history
    
    def train_walk_forward(self, features_array: np.ndarray,
                          train_ratio: float = 0.7,
                          val_ratio: float = 0.15,
                          lookback: int = 60,
                          prediction_horizon: int = 4,
                          epochs: int = 50,
                          batch_size: int = 32) -> dict:
        """
        Train model using walk-forward validation (no future-looking).
        
        Args:
            features_array: (N, seq_len, num_features) array of features
            train_ratio: Ratio for training data
            val_ratio: Ratio for validation data
            lookback: Lookback period (used only if 2D input)
            prediction_horizon: Prediction horizon
            epochs: Number of epochs
            batch_size: Batch size
        
        Returns:
            Training results
        """
        logger.info(f"Starting walk-forward training on {len(features_array)} samples")
        
        # If 3D already, use directly; otherwise prepare sequences
        if len(features_array.shape) == 3:
            # Already sequenced: (N, seq_len, num_features)
            X = features_array
            # Create targets: predict next period's direction
            y = np.zeros(len(X))
            for i in range(len(X) - 1):
                # Compare last close of current window with last close of next window
                current_close = features_array[i, -1, 0]  # Close of last timestep
                next_close = features_array[i+1, -1, 0]
                if current_close != 0:
                    y[i] = (next_close - current_close) / current_close
            
            logger.info(f"Using features directly: shape {X.shape}")
        else:
            # 2D features: need to sequence
            X, y = self.prepare_sequences(features_array, lookback, prediction_horizon)
        
        # Split data (no mixing!)
        n_samples = len(X)
        train_end = int(n_samples * train_ratio)
        val_end = train_end + int(n_samples * val_ratio)
        
        X_train, y_train = X[:train_end], y[:train_end]
        X_val, y_val = X[train_end:val_end], y[train_end:val_end]
        X_test, y_test = X[val_end:], y[val_end:]
        
        logger.info(f"Split: Train={len(X_train)}, Val={len(X_val)}, Test={len(X_test)}")
        
        # Train
        history = self.train(X_train, y_train, X_val, y_val,
                            epochs=epochs, batch_size=batch_size)
        
        # Evaluate on test set
        test_dataset = TensorDataset(
            torch.FloatTensor(X_test),
            torch.FloatTensor(y_test)
        )
        test_loader = DataLoader(test_dataset, batch_size=batch_size)
        test_loss, test_mae = self.validate(test_loader)
        
        history['test_loss'] = test_loss
        history['test_mae'] = test_mae
        
        logger.info(f"Test Results - Loss: {test_loss:.6f}, MAE: {test_mae:.6f}")
        
        return history
    
    def get_learning_rate(self) -> float:
        """Get current learning rate"""
        return self.optimizer.param_groups[0]['lr']
    
    def set_learning_rate(self, lr: float):
        """Set learning rate"""
        for param_group in self.optimizer.param_groups:
            param_group['lr'] = lr
        logger.info(f"Learning rate set to {lr}")
