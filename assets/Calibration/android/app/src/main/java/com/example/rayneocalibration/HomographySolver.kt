package com.example.rayneocalibration

import kotlin.math.abs
import kotlin.math.sqrt

object HomographySolver {
    data class PairPoint(
        val cameraX: Double,
        val cameraY: Double,
        val displayX: Double,
        val displayY: Double,
        val captureFile: String,
    )

    data class Result(
        val matrix: DoubleArray,
        val rmsError: Double,
        val maxError: Double,
        val inlierCount: Int,
    )

    fun solveRobust(points: List<PairPoint>, threshold: Double = 0.035): Result {
        require(points.size >= 4) { "至少需要 4 个有效点" }
        var bestInliers: List<PairPoint> = emptyList()
        var bestError = Double.POSITIVE_INFINITY
        for (indices in combinations(points.indices.toList(), 4)) {
            val candidate = solveLeastSquares(indices.map { points[it] }) ?: continue
            val inliers = points.filter { error(candidate, it) <= threshold }
            if (inliers.size < 4) continue
            val meanError = inliers.sumOf { error(candidate, it) } / inliers.size
            if (
                inliers.size > bestInliers.size ||
                (inliers.size == bestInliers.size && meanError < bestError)
            ) {
                bestInliers = inliers
                bestError = meanError
            }
        }
        if (bestInliers.size < 6) {
            throw IllegalStateException("有效标定点不足，请重新采集九点")
        }
        val matrix = solveLeastSquares(bestInliers)
            ?: throw IllegalStateException("H 矩阵求解失败")
        val errors = points.map { error(matrix, it) }
        return Result(
            matrix = matrix,
            rmsError = sqrt(errors.sumOf { it * it } / errors.size),
            maxError = errors.maxOrNull() ?: 0.0,
            inlierCount = bestInliers.size,
        )
    }

    fun project(matrix: DoubleArray, x: Double, y: Double): Pair<Double, Double> {
        val denominator = matrix[6] * x + matrix[7] * y + matrix[8]
        if (abs(denominator) < 1e-10) return Double.NaN to Double.NaN
        val u = (matrix[0] * x + matrix[1] * y + matrix[2]) / denominator
        val v = (matrix[3] * x + matrix[4] * y + matrix[5]) / denominator
        return u to v
    }

    private fun error(matrix: DoubleArray, point: PairPoint): Double {
        val projected = project(matrix, point.cameraX, point.cameraY)
        val dx = projected.first - point.displayX
        val dy = projected.second - point.displayY
        return sqrt(dx * dx + dy * dy)
    }

    private fun solveLeastSquares(points: List<PairPoint>): DoubleArray? {
        val ata = Array(8) { DoubleArray(8) }
        val atb = DoubleArray(8)
        for (point in points) {
            val x = point.cameraX
            val y = point.cameraY
            val u = point.displayX
            val v = point.displayY
            accumulate(
                doubleArrayOf(x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y),
                u,
                ata,
                atb,
            )
            accumulate(
                doubleArrayOf(0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y),
                v,
                ata,
                atb,
            )
        }
        val solution = gaussianSolve(ata, atb) ?: return null
        return doubleArrayOf(
            solution[0], solution[1], solution[2],
            solution[3], solution[4], solution[5],
            solution[6], solution[7], 1.0,
        )
    }

    private fun accumulate(
        row: DoubleArray,
        value: Double,
        ata: Array<DoubleArray>,
        atb: DoubleArray,
    ) {
        for (i in row.indices) {
            atb[i] += row[i] * value
            for (j in row.indices) {
                ata[i][j] += row[i] * row[j]
            }
        }
    }

    private fun gaussianSolve(matrix: Array<DoubleArray>, rhs: DoubleArray): DoubleArray? {
        val n = rhs.size
        val augmented = Array(n) { row ->
            DoubleArray(n + 1).also {
                matrix[row].copyInto(it)
                it[n] = rhs[row]
            }
        }
        for (column in 0 until n) {
            var pivot = column
            for (row in column + 1 until n) {
                if (abs(augmented[row][column]) > abs(augmented[pivot][column])) {
                    pivot = row
                }
            }
            if (abs(augmented[pivot][column]) < 1e-10) return null
            val swap = augmented[column]
            augmented[column] = augmented[pivot]
            augmented[pivot] = swap
            val divisor = augmented[column][column]
            for (j in column until n + 1) augmented[column][j] /= divisor
            for (row in 0 until n) {
                if (row == column) continue
                val factor = augmented[row][column]
                for (j in column until n + 1) {
                    augmented[row][j] -= factor * augmented[column][j]
                }
            }
        }
        return DoubleArray(n) { augmented[it][n] }
    }

    private fun <T> combinations(items: List<T>, size: Int): List<List<T>> {
        val result = mutableListOf<List<T>>()
        fun collect(start: Int, current: MutableList<T>) {
            if (current.size == size) {
                result.add(current.toList())
                return
            }
            for (index in start until items.size) {
                current.add(items[index])
                collect(index + 1, current)
                current.removeAt(current.lastIndex)
            }
        }
        collect(0, mutableListOf())
        return result
    }
}
