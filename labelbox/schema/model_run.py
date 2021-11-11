from typing import Dict, Iterable, Union
from pathlib import Path
import os
import time
import warnings

from labelbox.pagination import PaginatedCollection
from labelbox.schema.annotation_import import MEAPredictionImport
from labelbox.orm.query import results_query_part
from labelbox.orm.model import Field, Relationship
from labelbox.orm.db_object import DbObject


class ModelRun(DbObject):
    name = Field.String("name")
    updated_at = Field.DateTime("updated_at")
    created_at = Field.DateTime("created_at")
    created_by_id = Field.String("created_by_id", "createdBy")
    model_id = Field.String("model_id")

    def upsert_labels(self, label_ids, timeout_seconds=60):
        """ Adds data rows and labels to a model run
        Args:
            label_ids (list): label ids to insert
            timeout_seconds (float): Max waiting time, in seconds.
        Returns:
            ID of newly generated async task
        """

        if len(label_ids) < 1:
            raise ValueError("Must provide at least one label id")

        mutation_name = 'createMEAModelRunLabelRegistrationTask'
        create_task_query_str = """mutation createMEAModelRunLabelRegistrationTaskPyApi($modelRunId: ID!, $labelIds : [ID!]!) {
          %s(where : { id : $modelRunId}, data : {labelIds: $labelIds})}
          """ % (mutation_name)

        res = self.client.execute(create_task_query_str, {
            'modelRunId': self.uid,
            'labelIds': label_ids
        })
        task_id = res[mutation_name]

        status_query_str = """query MEALabelRegistrationTaskStatusPyApi($where: WhereUniqueIdInput!){
            MEALabelRegistrationTaskStatus(where: $where) {status errorMessage}
        }
        """
        return self._wait_until_done(lambda: self.client.execute(
            status_query_str, {'where': {
                'id': task_id
            }})['MEALabelRegistrationTaskStatus'],
                                     timeout_seconds=timeout_seconds)

    def upsert_data_rows(self, data_row_ids, timeout_seconds=60):
        """ Adds data rows to a model run without any associated labels
        Args:
            data_row_ids (list): data row ids to add to mea
            timeout_seconds (float): Max waiting time, in seconds.
        Returns:
            ID of newly generated async task
        """

        if len(data_row_ids) < 1:
            raise ValueError("Must provide at least one data row id")

        mutation_name = 'createMEAModelRunDataRowRegistrationTask'
        create_task_query_str = """mutation createMEAModelRunDataRowRegistrationTaskPyApi($modelRunId: ID!, $dataRowIds : [ID!]!) {
          %s(where : { id : $modelRunId}, data : {dataRowIds: $dataRowIds})}
          """ % (mutation_name)

        res = self.client.execute(create_task_query_str, {
            'modelRunId': self.uid,
            'dataRowIds': data_row_ids
        })
        task_id = res[mutation_name]

        status_query_str = """query MEADataRowRegistrationTaskStatusPyApi($where: WhereUniqueIdInput!){
            MEADataRowRegistrationTaskStatus(where: $where) {status errorMessage}
        }
        """
        return self._wait_until_done(lambda: self.client.execute(
            status_query_str, {'where': {
                'id': task_id
            }})['MEADataRowRegistrationTaskStatus'],
                                     timeout_seconds=timeout_seconds)

    def _wait_until_done(self, status_fn, timeout_seconds=60, sleep_time=5):
        # Do not use this function outside of the scope of upsert_data_rows or upsert_labels. It could change.
        while True:
            res = status_fn()
            if res['status'] == 'COMPLETE':
                return True
            elif res['status'] == 'FAILED':
                raise Exception(
                    f"MEA Import Failed. Details : {res['errorMessage']}")
            timeout_seconds -= sleep_time
            if timeout_seconds <= 0:
                raise TimeoutError(
                    f"Unable to complete import within {timeout_seconds} seconds."
                )

            time.sleep(sleep_time)

    def add_predictions(
        self,
        name: str,
        predictions: Union[str, Path, Iterable[Dict]],
    ) -> 'MEAPredictionImport':  # type: ignore
        """ Uploads predictions to a new Editor project.
        Args:
            name (str): name of the AnnotationImport job
            predictions (str or Path or Iterable):
                url that is publicly accessible by Labelbox containing an
                ndjson file
                OR local path to an ndjson file
                OR iterable of annotation rows
        Returns:
            AnnotationImport
        """
        kwargs = dict(client=self.client, model_run_id=self.uid, name=name)
        if isinstance(predictions, str) or isinstance(predictions, Path):
            if os.path.exists(predictions):
                return MEAPredictionImport.create_from_file(
                    path=str(predictions), **kwargs)
            else:
                return MEAPredictionImport.create_from_url(
                    url=str(predictions), **kwargs)
        elif isinstance(predictions, Iterable):
            return MEAPredictionImport.create_from_objects(
                predictions=predictions, **kwargs)
        else:
            raise ValueError(
                f'Invalid predictions given of type: {type(predictions)}')

    def model_run_data_rows(self):
        query_str = """query modelRunPyApi($modelRunId: ID!, $from : String, $first: Int){
                annotationGroups(where: {modelRunId: {id: $modelRunId}}, after: $from, first: $first)
                {nodes{%s},pageInfo{endCursor}}
            }
        """ % (results_query_part(ModelRunDataRow))
        return PaginatedCollection(
            self.client, query_str, {'modelRunId': self.uid},
            ['annotationGroups', 'nodes'],
            lambda client, res: ModelRunDataRow(client, self.model_id, res),
            ['annotationGroups', 'pageInfo', 'endCursor'])

    def annotation_groups(self):
        """
        "ModelRun.annotation_groups is being deprecated after version 3.9 
            in favor of ModelRun.model_run_data_rows"
        """
        warnings.warn(
            "`ModelRun.annotation_groups` is being deprecated in favor of `ModelRun.model_run_data_rows`"
        )
        return self.model_run_data_rows()

    def delete(self):
        """ Deletes specified model run.

        Returns:
            Query execution success.
        """
        ids_param = "ids"
        query_str = """mutation DeleteModelRunPyApi($%s: ID!) {
            deleteModelRuns(where: {ids: [$%s]})}""" % (ids_param, ids_param)
        self.client.execute(query_str, {ids_param: str(self.uid)})

    def delete_model_run_data_rows(self, data_row_ids):
        """ Deletes data rows from model runs.

        Args:
            data_row_ids (list): List of data row ids to delete from the model run.
        Returns:
            Query execution success.
        """
        model_run_id_param = "modelRunId"
        data_row_ids_param = "dataRowIds"
        query_str = """mutation DeleteModelRunDataRowsPyApi($%s: ID!, $%s: [ID!]!) {
            deleteModelRunDataRows(where: {modelRunId: $%s, dataRowIds: $%s})}""" % (
            model_run_id_param, data_row_ids_param, model_run_id_param,
            data_row_ids_param)
        self.client.execute(query_str, {
            model_run_id_param: self.uid,
            data_row_ids_param: data_row_ids
        })

    def delete_annotation_groups(self, data_row_ids):
        """
        "ModelRun.delete_annotation_groups is being deprecated after version 3.9 
            in favor of ModelRun.delete_model_run_data_rows"
        """
        warnings.warn(
            "`ModelRun.delete_annotation_groups` is being deprecated in favor of `ModelRun.delete_model_run_data_rows`"
        )
        return self.delete_model_run_data_rows(data_row_ids)


class ModelRunDataRow(DbObject):
    label_id = Field.String("label_id")
    model_run_id = Field.String("model_run_id")
    data_row = Relationship.ToOne("DataRow", False, cache=True)

    def __init__(self, client, model_id, *args, **kwargs):
        super().__init__(client, *args, **kwargs)
        self.model_id = model_id

    @property
    def url(self):
        app_url = self.client.app_url
        endpoint = f"{app_url}/models/{self.model_id}/{self.model_run_id}/AllDatarowsSlice/{self.uid}?view=carousel"
        return endpoint
