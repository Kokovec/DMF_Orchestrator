"""Problems and warnings, collected so that one run reports all of them."""


class Problem:
    def __init__(self, rule, where, message):
        self.rule = rule      # compile rule number from the spec, or "read"
        self.where = where    # "<function>: <parameter>" or a file/workload-level location
        self.message = message

    def __str__(self):
        return f"{self.where}: {self.message} [rule {self.rule}]"

    def __repr__(self):
        return f"Problem({self.rule!r}, {self.where!r}, {self.message!r})"


class CompileError(Exception):
    """Raised with every problem found; nothing has been written."""

    def __init__(self, problems, warnings=()):
        self.problems = list(problems)
        self.warnings = list(warnings)
        super().__init__("\n".join(str(p) for p in self.problems))


class Report:
    def __init__(self):
        self.problems = []
        self.warnings = []

    def refuse(self, rule, where, message):
        self.problems.append(Problem(rule, where, message))

    def warn(self, where, message):
        self.warnings.append(f"{where}: {message}")

    def raise_if_any(self):
        if self.problems:
            raise CompileError(self.problems, self.warnings)


def where(function, parameter=None):
    return f"{function}: {parameter}" if parameter else function
