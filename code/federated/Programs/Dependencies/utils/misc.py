from functools import wraps
from typing import Callable
from Compiler.types import sint, Matrix, cint
from Compiler.library import *
import Compiler.papers
import time


def sanitize_matrix(input: Matrix) -> Matrix:
    result = input.same_shape()
    
    input_vector = input.get_vector()
    comparison_vector = cint(val=1, size=len(input_vector))
    result.assign_vector(input_vector.greater_equal(comparison_vector))
    
    return result

def sanitize_matrix_inverse(input: Matrix) -> Matrix:
    result = input.same_shape()
    
    input_vector = input.get_vector()
    comparison_vector = cint(val=1, size=len(input_vector))
    result.assign_vector(input_vector.less_than(comparison_vector))
    
    return result

def sanitize_list(input: Array) -> Array:
    input_vector = input.get_vector()
    comparison_vector = cint(val=1, size=len(input_vector))

    return input_vector.greater_equal(comparison_vector)

def sanitize_list_inverse(input: Array) -> Array:
    input_vector = input.get_vector()
    comparison_vector = cint(val=1, size=len(input_vector))

    return input_vector.less_than(comparison_vector)

def create_diag_matrix(input: Array) -> Matrix:
    result = sint.Matrix(rows=len(input), columns=len(input))
    result.assign_all(cint(0))

    for i in range(len(input)):
        result[i][i] = input[i]

    return result

def create_row_entry_mask(input: Array) -> Matrix:
    input_matrix = Matrix.create_from(input)
    return input_matrix.mul_trans(input_matrix)


def create_row_entry_mask_inverse(input: Array) -> Matrix:
    inverse_matrix = cint.Matrix(rows=len(input), columns=len(input))
    inverse_matrix.assign_all(cint(1))
    input_matrix = Matrix.create_from(input)
    
    return inverse_matrix - input_matrix.mul_trans(input_matrix)

def mask_excluding_marked_row_columns(
        marking_array: Array
        ) -> Matrix:
    dim = len(marking_array)
    
    marking_matrix = sint.Matrix(rows=dim, columns=1)
    marking_matrix.assign_vector(marking_array.get_vector())

    ones_matrix = sint.Matrix(rows=1, columns=dim)
    ones_matrix.assign_vector(sint(1, size=dim))

    rows = marking_matrix.mul(ones_matrix)
    columns = ones_matrix.trans_mul(marking_matrix.transpose())

    return sanitize_matrix_inverse(rows + columns)