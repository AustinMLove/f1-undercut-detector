FROM public.ecr.aws/lambda/python:3.14

COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN pip install -r requirements.txt --target "${LAMBDA_TASK_ROOT}"

COPY f1_pipeline/ ${LAMBDA_TASK_ROOT}/f1_pipeline/
COPY candidate_search.py ${LAMBDA_TASK_ROOT}
COPY handler.py ${LAMBDA_TASK_ROOT}

CMD [ "handler.lambda_handler" ]