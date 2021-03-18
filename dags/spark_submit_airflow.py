from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.dummy_operator import DummyOperator
from airflow.hooks.S3_hook import S3Hook
from airflow.operators import PythonOperator
from airflow.contrib.operators.emr_create_job_flow_operator import (
    EmrCreateJobFlowOperator,
)
from airflow.contrib.operators.emr_add_steps_operator import EmrAddStepsOperator
from airflow.contrib.sensors.emr_step_sensor import EmrStepSensor
from airflow.contrib.operators.emr_terminate_job_flow_operator import (
    EmrTerminateJobFlowOperator,
)

# Configurations
BUCKET_NAME = "capestone-project-udacity-ciprian" # "spark-submit-airflow"  # replace this with your bucket name
local_data = "./dags/data/movie_review.csv"
s3_data = "data/movie_review.csv"
local_script = "./dags/scripts/spark/random_text_classification.py"
s3_script = "scripts/random_text_classification.py"
s3_weather_script = "scripts/emr/weather_data.py"
s3_clean = "clean_data/"

# {
#         "Name": "Move raw data from S3 to HDFS",
#         "ActionOnFailure": "CANCEL_AND_WAIT",
#         "HadoopJarStep": {
#             "Jar": "command-runner.jar",
#             "Args": [
#                 "s3-dist-cp",
#                 "--src=s3://{{ params.BUCKET_NAME }}/data",
#                 "--dest=/movie",
#             ],
#         },
#     },
# {
#         "Name": "Move clean data from HDFS to S3",
#         "ActionOnFailure": "CANCEL_AND_WAIT",
#         "HadoopJarStep": {
#             "Jar": "command-runner.jar",
#             "Args": [
#                 "s3-dist-cp",
#                 "--src=/output",
#                 "--dest=s3://{{ params.BUCKET_NAME }}/{{ params.s3_clean }}",
#             ],
#         },
#     },

SPARK_STEPS = [ # Note the params values are supplied to the operator

    {
        "Name": "Classify movie reviews",
        "ActionOnFailure": "CANCEL_AND_WAIT",
        "HadoopJarStep": {
            "Jar": "command-runner.jar",
            "Args": [
                "spark-submit",
                "--master",
                "yarn"
                "--deploy-mode",
                "cluster",
                "s3://{{ params.BUCKET_NAME }}/{{ params.s3_weather_script }}",
            ],
        },
    }

]

JOB_FLOW_OVERRIDES = {
    "Name": "Movie review classifier",
    "ReleaseLabel": "emr-5.28.0",
    "Applications": [{"Name": "Hadoop"}, {"Name": "Spark"}, {"Name": "Livy"}, {"Name": "Hive"}], # We want our EMR cluster to have HDFS and Spark
    "Configurations": [
        {
            "Classification": "spark-env",
            "Configurations": [
                {
                    "Classification": "export",
                    "Properties": {"PYSPARK_PYTHON": "/usr/bin/python3"}, # by default EMR uses py2, change it to py3
                }
            ],
        }
    ],
    "Instances": {
        "InstanceGroups": [
            {
                "Name": "Master node",
                "Market": "SPOT",
                "InstanceRole": "MASTER",
                "InstanceType": "m4.xlarge",
                "InstanceCount": 1,
            },
            {
                "Name": "Core - 2",
                "Market": "SPOT", # Spot instances are a "use as available" instances
                "InstanceRole": "CORE",
                "InstanceType": "m4.xlarge",
                "InstanceCount": 2,
            },
        ],
        "KeepJobFlowAliveWhenNoSteps": True,
        "TerminationProtected": False, # this lets us programmatically terminate the cluster
        'EmrManagedMasterSecurityGroup': 'sg-0a082a218a150eb9b',
        'EmrManagedSlaveSecurityGroup': 'sg-0c5aa56531af3a588',
        'Ec2KeyName': 'capestone'
    },

    "JobFlowRole": "EMR_EC2_DefaultRole",
    "ServiceRole": "EMR_DefaultRole",
}

# helper function
# def _local_to_s3(filename, key, bucket_name=BUCKET_NAME):
#     s3 = S3Hook()
#     s3.load_file(filename=filename, bucket_name=bucket_name, replace=True, key=key)


default_args = {
    "owner": "airflow",
    "depends_on_past": True,
    "wait_for_downstream": True,
    "start_date": datetime(2020, 10, 17),
    "email": ["airflow@airflow.com"],
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

dag = DAG(
    "spark_submit_airflow",
    default_args=default_args,
    schedule_interval=None,
    max_active_runs=1,
)

start_data_pipeline = DummyOperator(task_id="start_data_pipeline", dag=dag)

# data_to_s3 = PythonOperator(
#     dag=dag,
#     task_id="data_to_s3",
#     python_callable=_local_to_s3,
#     op_kwargs={"filename": local_data, "key": s3_data,},
# )
#
# script_to_s3 = PythonOperator(
#     dag=dag,
#     task_id="script_to_s3",
#     python_callable=_local_to_s3,
#     op_kwargs={"filename": local_script, "key": s3_script,},
# )

# Create an EMR cluster
create_emr_cluster = EmrCreateJobFlowOperator(
    task_id="create_emr_cluster",
    job_flow_overrides=JOB_FLOW_OVERRIDES,
    aws_conn_id="aws_default",
    emr_conn_id="emr_default",
    dag=dag,
)

# Add your steps to the EMR cluster
step_one = EmrAddStepsOperator(
    task_id="step_one",
    job_flow_id="{{ task_instance.xcom_pull(task_ids='create_emr_cluster', key='return_value') }}",
    aws_conn_id="aws_default",
    steps=SPARK_STEPS,
    params={ # these params are used to fill the paramterized values in SPARK_STEPS json
        "BUCKET_NAME": BUCKET_NAME,
        "s3_data": s3_data,
        "s3_script": s3_script,
        "s3_weather_script": s3_weather_script,
        "s3_clean": s3_clean,
    },
    dag=dag,
)

last_step = len(SPARK_STEPS) - 1 # this value will let the sensor know the last step to watch
# wait for the steps to complete
step_one_checker = EmrStepSensor(
    task_id="watch_step_one",
    job_flow_id="{{ task_instance.xcom_pull('create_emr_cluster', key='return_value') }}",
    step_id="{{ task_instance.xcom_pull(task_ids='step_one', key='return_value')["
    + str(last_step) + "] }}",
    aws_conn_id="aws_default",
    dag=dag,
)

# Terminate the EMR cluster
# terminate_emr_cluster = EmrTerminateJobFlowOperator(
#     task_id="terminate_emr_cluster",
#     job_flow_id="{{ task_instance.xcom_pull(task_ids='create_emr_cluster', key='return_value') }}",
#     aws_conn_id="aws_default",
#     dag=dag,
# )

# end_data_pipeline = DummyOperator(task_id="end_data_pipeline", dag=dag)

# start_data_pipeline >> [data_to_s3, script_to_s3] >> create_emr_cluster
start_data_pipeline >> create_emr_cluster >> step_one >> step_one_checker
# >> terminate_emr_cluster
# terminate_emr_cluster >> end_data_pipeline
