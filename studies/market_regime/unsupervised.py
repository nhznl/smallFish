"""Small deterministic K-means, diagonal GMM, and causal Gaussian HMM models.

The implementations deliberately expose only the operations this study needs.
They depend on NumPy alone, keep scaling outside the model, and make HMM
historical predictions with forward filtering rather than future-aware
smoothing or Viterbi decoding.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def logsumexp(values: np.ndarray, axis: int | None = None) -> np.ndarray:
    maximum = np.max(values, axis=axis, keepdims=True)
    shifted = np.exp(values - maximum)
    result = maximum + np.log(np.sum(shifted, axis=axis, keepdims=True))
    return np.squeeze(result, axis=axis) if axis is not None else result.squeeze()


@dataclass
class FoldScaler:
    mean_: np.ndarray
    scale_: np.ndarray

    @classmethod
    def fit(cls, values: np.ndarray) -> "FoldScaler":
        matrix = np.asarray(values, dtype=float)
        mean = matrix.mean(axis=0)
        scale = matrix.std(axis=0, ddof=0)
        scale = np.where(scale > 1e-12, scale, 1.0)
        return cls(mean, scale)

    def transform(self, values: np.ndarray) -> np.ndarray:
        return (np.asarray(values, dtype=float) - self.mean_) / self.scale_


def _kmeans_plus_plus(values: np.ndarray, components: int, rng) -> np.ndarray:
    centers = [values[int(rng.integers(len(values)))]]
    while len(centers) < components:
        distances = np.min(
            np.sum((values[:, None, :] - np.asarray(centers)[None, :, :]) ** 2, axis=2),
            axis=1,
        )
        total = float(distances.sum())
        if total <= 0:
            index = int(rng.integers(len(values)))
        else:
            index = int(rng.choice(len(values), p=distances / total))
        centers.append(values[index])
    return np.asarray(centers, dtype=float)


class KMeansModel:
    def __init__(self, components: int, seeds: list[int], max_iterations: int, tolerance: float):
        self.components = int(components)
        self.seeds = [int(seed) for seed in seeds]
        self.max_iterations = int(max_iterations)
        self.tolerance = float(tolerance)
        self.means_: np.ndarray | None = None
        self.objective_: float | None = None
        self.iterations_: int = 0
        self.converged_: bool = False

    def fit(self, values: np.ndarray) -> "KMeansModel":
        matrix = np.asarray(values, dtype=float)
        if len(matrix) < self.components:
            raise ValueError("K-means training rows are fewer than components")
        best = None
        for seed in self.seeds:
            rng = np.random.default_rng(seed)
            means = _kmeans_plus_plus(matrix, self.components, rng)
            converged = False
            for iteration in range(1, self.max_iterations + 1):
                distances = np.sum((matrix[:, None, :] - means[None, :, :]) ** 2, axis=2)
                labels = np.argmin(distances, axis=1)
                updated = means.copy()
                for component in range(self.components):
                    members = matrix[labels == component]
                    if len(members):
                        updated[component] = members.mean(axis=0)
                    else:
                        updated[component] = matrix[int(rng.integers(len(matrix)))]
                shift = float(np.max(np.linalg.norm(updated - means, axis=1)))
                means = updated
                if shift <= self.tolerance:
                    converged = True
                    break
            distances = np.sum((matrix[:, None, :] - means[None, :, :]) ** 2, axis=2)
            objective = float(np.min(distances, axis=1).sum())
            candidate = (objective, means.copy(), iteration, converged)
            if best is None or candidate[0] < best[0]:
                best = candidate
        self.objective_, self.means_, self.iterations_, self.converged_ = best
        return self

    def predict(self, values: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
        distances = np.sum(
            (np.asarray(values, dtype=float)[:, None, :] - self.means_[None, :, :]) ** 2,
            axis=2,
        )
        return np.argmin(distances, axis=1), None


def _diag_log_density(values: np.ndarray, means: np.ndarray, variances: np.ndarray) -> np.ndarray:
    difference = values[:, None, :] - means[None, :, :]
    return -0.5 * (
        np.sum(np.log(2.0 * np.pi * variances), axis=1)[None, :]
        + np.sum(difference * difference / variances[None, :, :], axis=2)
    )


class GaussianMixtureModel:
    def __init__(self, components: int, seeds: list[int], max_iterations: int,
                 tolerance: float, minimum_variance: float):
        self.components = int(components)
        self.seeds = [int(seed) for seed in seeds]
        self.max_iterations = int(max_iterations)
        self.tolerance = float(tolerance)
        self.minimum_variance = float(minimum_variance)
        self.weights_: np.ndarray | None = None
        self.means_: np.ndarray | None = None
        self.variances_: np.ndarray | None = None
        self.objective_: float | None = None
        self.iterations_: int = 0
        self.converged_: bool = False

    def _fit_one(self, matrix: np.ndarray, seed: int):
        kmeans = KMeansModel(
            self.components, [seed], min(self.max_iterations, 30), self.tolerance).fit(matrix)
        means = kmeans.means_.copy()
        labels, _ = kmeans.predict(matrix)
        global_variance = np.maximum(matrix.var(axis=0), self.minimum_variance)
        variances = np.vstack([
            np.maximum(matrix[labels == component].var(axis=0), self.minimum_variance)
            if np.sum(labels == component) > 1 else global_variance
            for component in range(self.components)
        ])
        weights = np.bincount(labels, minlength=self.components).astype(float) + 1.0
        weights /= weights.sum()
        previous = -np.inf
        converged = False
        for iteration in range(1, self.max_iterations + 1):
            log_weighted = _diag_log_density(matrix, means, variances) + np.log(weights)[None, :]
            row_log = logsumexp(log_weighted, axis=1)
            likelihood = float(row_log.sum())
            responsibilities = np.exp(log_weighted - row_log[:, None])
            totals = np.maximum(responsibilities.sum(axis=0), 1e-12)
            weights = totals / totals.sum()
            means = responsibilities.T @ matrix / totals[:, None]
            difference = matrix[:, None, :] - means[None, :, :]
            variances = (
                np.sum(responsibilities[:, :, None] * difference * difference, axis=0)
                / totals[:, None]
            )
            variances = np.maximum(variances, self.minimum_variance)
            if np.isfinite(previous) and abs(likelihood - previous) <= self.tolerance * (1 + abs(previous)):
                converged = True
                break
            previous = likelihood
        return likelihood, weights, means, variances, iteration, converged

    def fit(self, values: np.ndarray) -> "GaussianMixtureModel":
        matrix = np.asarray(values, dtype=float)
        best = None
        for seed in self.seeds:
            candidate = self._fit_one(matrix, seed)
            if best is None or candidate[0] > best[0]:
                best = candidate
        (self.objective_, self.weights_, self.means_, self.variances_,
         self.iterations_, self.converged_) = best
        return self

    def predict(self, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        matrix = np.asarray(values, dtype=float)
        log_weighted = (
            _diag_log_density(matrix, self.means_, self.variances_)
            + np.log(self.weights_)[None, :]
        )
        probabilities = np.exp(log_weighted - logsumexp(log_weighted, axis=1)[:, None])
        return np.argmax(probabilities, axis=1), probabilities


def _forward_backward(start: np.ndarray, transition: np.ndarray,
                      log_emission: np.ndarray):
    row_max = np.max(log_emission, axis=1)
    emission = np.exp(log_emission - row_max[:, None])
    rows, components = emission.shape
    alpha = np.zeros((rows, components), dtype=float)
    scales = np.zeros(rows, dtype=float)
    alpha[0] = start * emission[0]
    scales[0] = max(float(alpha[0].sum()), 1e-300)
    alpha[0] /= scales[0]
    for index in range(1, rows):
        alpha[index] = (alpha[index - 1] @ transition) * emission[index]
        scales[index] = max(float(alpha[index].sum()), 1e-300)
        alpha[index] /= scales[index]

    beta = np.ones((rows, components), dtype=float)
    for index in range(rows - 2, -1, -1):
        beta[index] = transition @ (emission[index + 1] * beta[index + 1])
        beta[index] /= scales[index + 1]
    gamma = alpha * beta
    gamma /= np.maximum(gamma.sum(axis=1, keepdims=True), 1e-300)

    xi_sum = np.zeros_like(transition)
    for index in range(rows - 1):
        xi = (
            alpha[index, :, None] * transition
            * (emission[index + 1] * beta[index + 1])[None, :]
        )
        xi_sum += xi / max(float(xi.sum()), 1e-300)
    likelihood = float(np.sum(np.log(scales) + row_max))
    return likelihood, alpha, gamma, xi_sum


class GaussianHMMModel:
    def __init__(self, components: int, seeds: list[int], max_iterations: int,
                 tolerance: float, minimum_variance: float):
        self.components = int(components)
        self.seeds = [int(seed) for seed in seeds]
        self.max_iterations = int(max_iterations)
        self.tolerance = float(tolerance)
        self.minimum_variance = float(minimum_variance)
        self.start_: np.ndarray | None = None
        self.transition_: np.ndarray | None = None
        self.means_: np.ndarray | None = None
        self.variances_: np.ndarray | None = None
        self.last_alpha_: np.ndarray | None = None
        self.objective_: float | None = None
        self.iterations_: int = 0
        self.converged_: bool = False

    def _fit_one(self, matrix: np.ndarray, seed: int):
        kmeans = KMeansModel(
            self.components, [seed], min(self.max_iterations, 30), self.tolerance).fit(matrix)
        means = kmeans.means_.copy()
        labels, _ = kmeans.predict(matrix)
        global_variance = np.maximum(matrix.var(axis=0), self.minimum_variance)
        variances = np.vstack([
            np.maximum(matrix[labels == component].var(axis=0), self.minimum_variance)
            if np.sum(labels == component) > 1 else global_variance
            for component in range(self.components)
        ])
        start = np.full(self.components, 1.0)
        start[labels[0]] += 4.0
        start /= start.sum()
        transition = np.ones((self.components, self.components), dtype=float)
        for left, right in zip(labels[:-1], labels[1:]):
            transition[left, right] += 1.0
        transition /= transition.sum(axis=1, keepdims=True)

        previous = -np.inf
        converged = False
        for iteration in range(1, self.max_iterations + 1):
            log_emission = _diag_log_density(matrix, means, variances)
            likelihood, alpha, gamma, xi_sum = _forward_backward(
                start, transition, log_emission)
            totals = np.maximum(gamma.sum(axis=0), 1e-12)
            start = np.maximum(gamma[0], 1e-6)
            start /= start.sum()
            transition = xi_sum + 1e-3
            transition /= transition.sum(axis=1, keepdims=True)
            means = gamma.T @ matrix / totals[:, None]
            difference = matrix[:, None, :] - means[None, :, :]
            variances = (
                np.sum(gamma[:, :, None] * difference * difference, axis=0)
                / totals[:, None]
            )
            variances = np.maximum(variances, self.minimum_variance)
            if np.isfinite(previous) and abs(likelihood - previous) <= self.tolerance * (1 + abs(previous)):
                converged = True
                break
            previous = likelihood
        final_log = _diag_log_density(matrix, means, variances)
        final_likelihood, alpha, _, _ = _forward_backward(start, transition, final_log)
        return (final_likelihood, start, transition, means, variances,
                alpha[-1].copy(), iteration, converged)

    def fit(self, values: np.ndarray) -> "GaussianHMMModel":
        matrix = np.asarray(values, dtype=float)
        best = None
        for seed in self.seeds:
            candidate = self._fit_one(matrix, seed)
            if best is None or candidate[0] > best[0]:
                best = candidate
        (self.objective_, self.start_, self.transition_, self.means_, self.variances_,
         self.last_alpha_, self.iterations_, self.converged_) = best
        return self

    def predict(self, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Causal filtering: each output uses training plus rows through that row."""
        matrix = np.asarray(values, dtype=float)
        log_emission = _diag_log_density(matrix, self.means_, self.variances_)
        row_max = np.max(log_emission, axis=1)
        emission = np.exp(log_emission - row_max[:, None])
        probabilities = np.zeros((len(matrix), self.components), dtype=float)
        posterior = self.last_alpha_.copy()
        for index in range(len(matrix)):
            prior = posterior @ self.transition_
            posterior = prior * emission[index]
            posterior /= max(float(posterior.sum()), 1e-300)
            probabilities[index] = posterior
        return np.argmax(probabilities, axis=1), probabilities


def risk_rank(means: np.ndarray, feature_names: list[str]) -> np.ndarray:
    """Map arbitrary state IDs to low-to-high risk using training properties."""
    weights = {
        "return_20": -1.0,
        "distance_sma_200": -1.0,
        "log_rv_20": 1.0,
        "log_vix": 1.0,
    }
    vector = np.asarray([weights[name] for name in feature_names], dtype=float)
    scores = np.asarray(means, dtype=float) @ vector
    order = np.argsort(scores, kind="stable")
    ranks = np.empty(len(order), dtype=int)
    ranks[order] = np.arange(len(order))
    return ranks


def ranked_state_outputs(states: np.ndarray, ranks: np.ndarray,
                         probabilities: np.ndarray | None) -> tuple[list[str], np.ndarray, np.ndarray]:
    components = len(ranks)
    state_ranks = ranks[np.asarray(states, dtype=int)]
    labels = [f"RISK_{int(rank)+1}_OF_{components}" for rank in state_ranks]
    exposure = 1.0 - state_ranks / max(components - 1, 1)
    state_probability = (
        np.full(len(states), np.nan) if probabilities is None
        else probabilities[np.arange(len(states)), np.asarray(states, dtype=int)]
    )
    return labels, exposure.astype(float), state_probability.astype(float)
