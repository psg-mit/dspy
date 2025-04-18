import logging
import types
from typing import TYPE_CHECKING, Any, Callable, List, Optional, Union, Tuple

if TYPE_CHECKING:
    import pandas as pd

import tqdm
from copy import deepcopy
import dspy
from dspy.utils.callback import with_callbacks
from dspy.utils.parallelizer import ParallelExecutor

try:
    from IPython.display import HTML
    from IPython.display import display as display

except ImportError:

    def display(obj: Any):
        """
        Display the specified Python object in the console.

        :param obj: The Python object to display.
        """
        print(obj)

    def HTML(x: str) -> str:
        """
        Obtain the HTML representation of the specified string.
        """
        # NB: This method exists purely for code compatibility with the IPython HTML() function in
        # environments where IPython is not available. In such environments where IPython is not
        # available, this method will simply return the input string.
        return x


# TODO: Counting failures and having a max_failure count. When that is exceeded (also just at the end),
# we print the number of failures, the first N examples that failed, and the first N exceptions raised.

logger = logging.getLogger(__name__)


class Evaluate:
    """DSPy Evaluate class.

    This class is used to evaluate the performance of a DSPy program. Users need to provide a evaluation dataset and
    a metric function in order to use this class. This class supports parallel evaluation on the provided dataset.
    """

    def __init__(
        self,
        *,
        devset: List["dspy.Example"],
        metric: Optional[Callable] = None,
        num_threads: int = 1,
        display_progress: bool = False,
        display_table: Union[bool, int] = False,
        max_errors: int = 5,
        return_all_scores: bool = False,
        return_outputs: bool = False,
        provide_traceback: bool = False,
        failure_score: float = 0.0,
        **kwargs,
    ):
        """
        Args:
            devset (List[dspy.Example]): the evaluation dataset.
            metric (Callable): The metric function to use for evaluation.
            num_threads (int): The number of threads to use for parallel evaluation.
            display_progress (bool): Whether to display progress during evaluation.
            display_table (Union[bool, int]): Whether to display the evaluation results in a table.
                If a number is passed, the evaluation results will be truncated to that number before displayed.
            max_errors (int): The maximum number of errors to allow before stopping evaluation.
            return_all_scores (bool): Whether to return scores for every data record in `devset`.
            return_outputs (bool): Whether to return the dspy program's outputs for every data in `devset`.
            provide_traceback (bool): Whether to provide traceback information during evaluation.
            failure_score (float): The default score to use if evaluation fails due to an exception.
        """
        self.devset = devset
        self.metric = metric
        self.num_threads = num_threads
        self.display_progress = display_progress
        self.display_table = display_table
        self.max_errors = max_errors
        self.return_all_scores = return_all_scores
        self.return_outputs = return_outputs
        self.provide_traceback = provide_traceback
        self.failure_score = failure_score

    @with_callbacks
    def __call__(
        self,
        program: "dspy.Module",
        metric: Optional[Callable] = None,
        devset: Optional[List["dspy.Example"]] = None,
        num_threads: Optional[int] = None,
        display_progress: Optional[bool] = None,
        display_table: Optional[Union[bool, int]] = None,
        return_all_scores: Optional[bool] = None,
        return_outputs: Optional[bool] = None,
        callback_metadata: Optional[dict[str, Any]] = None,
    ):
        """
        Args:
            program (dspy.Module): The DSPy program to evaluate.
            metric (Callable): The metric function to use for evaluation. if not provided, use `self.metric`.
            devset (List[dspy.Example]): the evaluation dataset. if not provided, use `self.devset`.
            num_threads (int): The number of threads to use for parallel evaluation. if not provided, use
                `self.num_threads`.
            display_progress (bool): Whether to display progress during evaluation. if not provided, use
                `self.display_progress`.
            display_table (Union[bool, int]): Whether to display the evaluation results in a table. if not provided, use
                `self.display_table`. If a number is passed, the evaluation results will be truncated to that number before displayed.
            return_all_scores (bool): Whether to return scores for every data record in `devset`. if not provided,
                use `self.return_all_scores`.
            return_outputs (bool): Whether to return the dspy program's outputs for every data in `devset`. if not
                provided, use `self.return_outputs`.
            callback_metadata (dict): Metadata to be used for evaluate callback handlers.

        Returns:
            The evaluation results are returned in different formats based on the flags:

            - Base return: A float percentage score (e.g., 67.30) representing overall performance

            - With `return_all_scores=True`:
                Returns (overall_score, individual_scores) where individual_scores is a list of
                float scores for each example in devset

            - With `return_outputs=True`:
                Returns (overall_score, result_triples) where result_triples is a list of
                (example, prediction, score) tuples for each example in devset

            - With both flags=True:
                Returns (overall_score, result_triples, individual_scores)

        """
        metric = metric if metric is not None else self.metric
        devset = devset if devset is not None else self.devset
        num_threads = num_threads if num_threads is not None else self.num_threads
        display_progress = display_progress if display_progress is not None else self.display_progress
        display_table = display_table if display_table is not None else self.display_table
        return_all_scores = return_all_scores if return_all_scores is not None else self.return_all_scores
        return_outputs = return_outputs if return_outputs is not None else self.return_outputs

        if callback_metadata:
            logger.debug(f"Evaluate is called with callback metadata: {callback_metadata}")

        tqdm.tqdm._instances.clear()

        executor = ParallelExecutor(
            num_threads=num_threads,
            disable_progress_bar=not display_progress,
            max_errors=self.max_errors,
            provide_traceback=self.provide_traceback,
            compare_results=True,
            timeout=300,
        )

        def process_item(example):
            # prediction = program(**example.inputs())
            # score = metric(example, prediction)
            try:
                prediction = program(**example.inputs())
                score = metric(example, prediction)
            except Exception as e:
                print(example.program_name, ">> Error:", e)
                # For nightjar, we want to pass along the error, because nightjar can raise errors
                prediction = dspy.Prediction(outputs=None, trajectory=None, error=e)
                score = metric(example, prediction)

            # Increment assert and suggest failures to program's attributes
            if hasattr(program, "_assert_failures"):
                program._assert_failures += dspy.settings.get("assert_failures")
            if hasattr(program, "_suggest_failures"):
                program._suggest_failures += dspy.settings.get("suggest_failures")

            return prediction, score

        results = executor.execute(process_item, devset)
        # results = []
        # for example in devset:
        #     try:
        #         prediction = program(**example.inputs())
        #         score = metric(example, prediction)
        #     except Exception as e:
        #         print(example.program_name, ">> Error:", e)
        #         # For nightjar, we want to pass along the error, because nightjar can raise errors
        #         prediction = dspy.Prediction(outputs=None, trajectory=None, error=e)
        #         score = metric(example, prediction)
        #         results.append((prediction, score))
        #         continue
        #     # Increment assert and suggest failures to program's attributes
        #     if hasattr(program, "_assert_failures"):
        #         program._assert_failures += dspy.settings.get("assert_failures")
        #     if hasattr(program, "_suggest_failures"):
        #         program._suggest_failures += dspy.settings.get("suggest_failures")

        #     results.append((prediction, score))

        assert len(devset) == len(results)

        results = [((dspy.Prediction(), self.failure_score) if r is None else r) for r in results]
        results = [(example, prediction, score) for example, (prediction, score) in zip(devset, results)]
        ncorrect, ntotal = sum(score for *_, score in results), len(devset)

        logger.info(f"Average Metric: {ncorrect} / {ntotal} ({round(100 * ncorrect / ntotal, 1)}%)")

        # Rename the 'correct' column to the name of the metric object
        metric_name = metric.__name__ if isinstance(metric, types.FunctionType) else metric.__class__.__name__
        # Construct a pandas DataFrame from the results
        result_df = self._construct_result_table(results, metric_name)

        if display_table:
            self._display_result_table(result_df, display_table, metric_name)

        if return_all_scores and return_outputs:
            return round(100 * ncorrect / ntotal, 2), results, [score for *_, score in results]
        if return_all_scores:
            return round(100 * ncorrect / ntotal, 2), [score for *_, score in results]
        if return_outputs:
            return round(100 * ncorrect / ntotal, 2), results

        return round(100 * ncorrect / ntotal, 2)

    def _construct_result_table(
        self, results: list[Tuple[dspy.Example, dspy.Example, Any]], metric_name: str
    ) -> "pd.DataFrame":
        """
        Construct a pandas DataFrame from the specified result list.
        Let's not try to change the name of this method as it may be patched by external tracing tools.

        Args:
            results: The list of results to construct the result DataFrame from.
            metric_name: The name of the metric used for evaluation.

        Returns:
            The constructed pandas DataFrame.
        """
        import pandas as pd

        data = [
            (
                merge_dicts(example, prediction) | {"correct": score}
                if prediction_is_dictlike(prediction)
                else dict(example) | {"prediction": prediction, "correct": score}
            )
            for example, prediction, score in results
        ]

        # Truncate every cell in the DataFrame (DataFrame.applymap was renamed to DataFrame.map in Pandas 2.1.0)
        result_df = pd.DataFrame(data)
        result_df = result_df.map(truncate_cell) if hasattr(result_df, "map") else result_df.applymap(truncate_cell)

        return result_df.rename(columns={"correct": metric_name})

    def _display_result_table(self, result_df: "pd.DataFrame", display_table: Union[bool, int], metric_name: str):
        """
        Display the specified result DataFrame in a table format.

        Args:
            result_df: The result DataFrame to display.
            display_table: Whether to display the evaluation results in a table.
                If a number is passed, the evaluation results will be truncated to that number before displayed.
            metric_name: The name of the metric used for evaluation.
        """
        if isinstance(display_table, bool):
            df_to_display = result_df.copy()
            truncated_rows = 0
        else:
            df_to_display = result_df.head(display_table).copy()
            truncated_rows = len(result_df) - display_table

        df_to_display = stylize_metric_name(df_to_display, metric_name)

        display_dataframe(df_to_display)

        if truncated_rows > 0:
            # Simplified message about the truncated rows
            message = f"""
            <div style='
                text-align: center;
                font-size: 16px;
                font-weight: bold;
                color: #555;
                margin: 10px 0;'>
                ... {truncated_rows} more rows not displayed ...
            </div>
            """
            display(HTML(message))


def prediction_is_dictlike(prediction):
    # Downstream logic for displaying dictionary-like predictions depends solely on the predictions
    # having a method called `items()` for iterating through key/value pairs
    return hasattr(prediction, "items") and callable(getattr(prediction, "items"))


def merge_dicts(d1, d2) -> dict:
    merged = {}
    for k, v in d1.items():
        if k in d2:
            merged[f"example_{k}"] = v
        else:
            merged[k] = v

    for k, v in d2.items():
        if k in d1:
            merged[f"pred_{k}"] = v
        else:
            merged[k] = v

    return merged


def truncate_cell(content) -> str:
    """Truncate content of a cell to 25 words."""
    words = str(content).split()
    if len(words) > 25:
        return " ".join(words[:25]) + "..."
    return content


def stylize_metric_name(df: "pd.DataFrame", metric_name: str) -> "pd.DataFrame":
    """
    Stylize the cell contents of a pandas DataFrame corresponding to the specified metric name.

    :param df: The pandas DataFrame for which to stylize cell contents.
    :param metric_name: The name of the metric for which to stylize DataFrame cell contents.
    """
    df[metric_name] = df[metric_name].apply(
        lambda x: f"✔️ [{x:.3f}]" if x and isinstance(x, float) else f"✔️ [{x}]" if x else ""
    )
    return df


def display_dataframe(df: "pd.DataFrame"):
    """
    Display the specified Pandas DataFrame in the console.

    :param df: The Pandas DataFrame to display.
    """
    import pandas as pd

    if is_in_ipython_notebook_environment():
        display(configure_dataframe_for_ipython_notebook_display(df))
    else:
        # Pretty print the DataFrame to the console
        with pd.option_context(
            "display.max_rows", None, "display.max_columns", None
        ):  # more options can be specified also
            print(df)


def configure_dataframe_for_ipython_notebook_display(df: "pd.DataFrame") -> "pd.DataFrame":
    """Set various pandas display options for DataFrame in an IPython notebook environment."""
    import pandas as pd

    pd.options.display.max_colwidth = 70
    return df


def is_in_ipython_notebook_environment():
    """
    Check if the current environment is an IPython notebook environment.

    :return: True if the current environment is an IPython notebook environment, False otherwise.
    """
    try:
        from IPython import get_ipython

        # This is a best-effort check to see if we are in an IPython notebook environment
        return "IPKernelApp" in getattr(get_ipython(), "config", {})
    except ImportError:
        return False


# FIXME: TODO: The merge_dicts stuff above is way too quick and dirty.
# TODO: the display_table can't handle False but can handle 0!
# Not sure how it works with True exactly, probably fails too.
