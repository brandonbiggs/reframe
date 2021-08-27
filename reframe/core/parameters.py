# Copyright 2016-2021 Swiss National Supercomputing Centre (CSCS/ETH Zurich)
# ReFrame Project Developers. See the top-level LICENSE file for details.
#
# SPDX-License-Identifier: BSD-3-Clause

#
# Functionality to build extensible parameterized tests.
#

import copy
import functools
import itertools

import reframe.core.namespaces as namespaces
from reframe.core.exceptions import ReframeSyntaxError


class TestParam:
    '''Regression test paramter class.

    Stores the attributes of a regression test parameter as defined directly
    in the test definition. These attributes are the parameter's name,
    values, and inheritance behaviour. This class should be thought of as a
    temporary storage for these parameter attributes, before the full final
    parameter space is built.

    :meta private:
    '''

    def __init__(self, values=None,
                 inherit_params=False, filter_params=None):
        if values is None:
            values = []

        # By default, filter out all the parameter values defined in the
        # base classes.
        if not inherit_params:
            def filter_params(x):
                return ()

        # If inherit_params==True, inherit all the parameter values from the
        # base classes as default behaviour.
        elif filter_params is None:
            def filter_params(x):
                return x

        self.values = tuple(values)
        self.filter_params = filter_params


class ParamSpace(namespaces.Namespace):
    ''' Regression test parameter space

    Host class for the parameter space of a regresion test. The parameter
    space is stored as a dictionary (self.params), where the keys are the
    parameter names and the values are tuples with all the available values
    for each parameter. The __init__ method in this class takes an optional
    argument (target_class), which is the regression test class where the
    parameter space is to e inserted as the ``_rfm_param_space`` class
    attribute. If no target class is provided, the parameter space is
    initialized as empty. After the parameter space is set, a parameter space
    iterator is created under self.__unique_iter, which acts as an internal
    control variable that tracks the usage of this parameter space. This
    iterator walks through all possible parameter combinations and cannot be
    restored after reaching exhaustion. The length of this iterator matches
    the value returned by the member function __len__.

    :param target_cls: the class where the full parameter space is to be built.
    :param target_namespace: a reference namespace to ensure that no name
        clashes occur (see :class:`reframe.core.namespaces.Namespace`).

    .. note::
        The __init__ method is aware of the implementation details of the
        regression test metaclass. This is required to retrieve the parameter
        spaces from the base classes, and also the local parameter space from
        the target class.
    '''

    @property
    def local_namespace_name(self):
        return '_rfm_local_param_space'

    @property
    def namespace_name(self):
        return '_rfm_param_space'

    def __init__(self, target_cls=None, target_namespace=None):
        super().__init__(target_cls, target_namespace)

        # Store all param combinations to allow random access.
        self.__random_access_iter = tuple(x for x in iter(self))

    def join(self, other, cls):
        '''Join other parameter space into the current one.

        Join two different parameter spaces into a single one. Both parameter
        spaces must be an instance ot the ParamSpace class. This method will
        raise an error if a parameter is defined in the two parameter spaces
        to be merged.

        :param other: instance of the ParamSpace class.
        :param cls: the target class.
        '''
        for key in other.params:
            # With multiple inheritance, a single parameter
            # could be doubly defined and lead to repeated
            # values
            if (key in self.params and
                self.params[key] != () and
                other.params[key] != ()):

                raise ReframeSyntaxError(
                    f'parameter space conflict: '
                    f'parameter {key!r} is defined in more than '
                    f'one base class of class {cls.__qualname__!r}'
                )

            self.params[key] = (
                other.params.get(key, ()) + self.params.get(key, ())
            )

    def extend(self, cls):
        '''Extend the parameter space with the local parameter space.'''

        local_param_space = getattr(cls, self.local_namespace_name)
        while(local_param_space):
            name, p = local_param_space.popitem()
            self.params[name] = (
                p.filter_params(self.params.get(name, ())) + p.values
            )

        # If any previously declared parameter was defined in the class body
        # by directly assigning it a value, raise an error. Parameters must be
        # changed using the `x = parameter([...])` syntax.
        for key, values in cls.__dict__.items():
            if key in self.params:
                raise ReframeSyntaxError(
                    f'parameter {key!r} must be modified through the built-in '
                    f'parameter type'
                )

    def inject(self, obj, cls=None, params_variant=None):
        '''Insert the params in the regression test.

        Create and initialize the regression test parameters as object
        attributes. The values assigned to these parameters exclusively depend
        on the value of params_variant. This argument is simply an index to a
        a given parametere combination. If params_variant is left with its
        default value, the regression test parameters are initialized as
        None.

        :param obj: The test object.
        :param cls: The test class.
        :param params_variant: index to a point in the parameter space.
        '''
        # Set the values of the test parameters (if any)
        if self.params and not params_variant is None:
            try:
                # Consume the parameter space iterator
                param_values = self.random_access_iter[params_variant]
                for index, key in enumerate(self.params):
                    setattr(obj, key, param_values[index])

            except IndexError as no_params:
                raise RuntimeError(
                    f'parameter space index out of range for '
                    f'{obj.__class__.__qualname__}'
                ) from None

        else:
            # Otherwise init the params as None
            for key in self.params:
                setattr(obj, key, None)

    def __iter__(self):
        '''Create a generator object to iterate over the parameter space

        The parameters must be deep-copied to prevent an instance from
        modifying the class parameter space.

        :return: generator object to iterate over the parameter space.
        '''
        yield from itertools.product(
            *(copy.deepcopy(p) for p in self.params.values())
        )

    @property
    def params(self):
        return self._namespace

    @property
    def random_access_iter(self):
        '''Expose the internal random access iterator as read-only'''
        return self.__random_access_iter

    def __len__(self):
        '''Returns the number of all possible parameter combinations.

        Method to calculate the test's parameter space length (i.e. the number
        of all possible parameter combinations). If the RegressionTest
        has no parameters, the length is 1.

        .. note::
           If the test is an abstract test (i.e. has undefined parameters in
           the parameter space), the returned parameter space length is 0.

        :return: length of the parameter space

        '''
        if not self.params:
            return 1

        return functools.reduce(
            lambda x, y: x*y,
            (len(p) for p in self.params.values())
        )

    def __getitem__(self, key):
        '''Access an element in the parameter space.

        If the key is an integer, this function will retrieve a given point in
        the parameter space. If the key is a parameter name, it will instead
        return all the values assigned to that parameter.
        '''
        if isinstance(key, int):
            ret = dict()
            val = self.random_access_iter[key]
            for i, key in enumerate(self.params):
                ret[key] = val[i]

            return ret

        return self.params.get(key, ())

    def is_empty(self):
        return self.params == {}

    def get_variant_nums(self, **conditions):
        '''Filter the paramter indices with a given set of conditions.

        The conditions are passed as key-value pairs, where the keys are the
        parameter names to apply the filtering on and the values are functions
        that expect the parameter's value as the sole argument.
        '''
        candidates = range(len(self.random_access_iter))
        if not conditions:
            return list(candidates)

        pos_map = {k: v for v, k in enumerate(self.keys())}
        def _get_param_value(name, variant):
            try:
                return self.random_access_iter[variant][pos_map[name]]
            except KeyError:
                raise KeyError(f'{name} is not present in the parameter space')

        for p, cond in conditions.items():
            val = lambda v: _get_param_value(p, v)
            candidates = list(filter(lambda v: cond(val(v)), candidates))

        return candidates





