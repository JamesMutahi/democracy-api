# Use the official Python runtime image
FROM python:3.12.3

# Create the app directory
RUN mkdir /democracy

# Set the working directory inside the container
WORKDIR /democracy

# Set environment variables
# Prevents Python from writing pyc files to disk
ENV PYTHONDONTWRITEBYTECODE=1
#Prevents Python from buffering stdout and stderr
ENV PYTHONUNBUFFERED=1

# Upgrade pip
RUN pip install --upgrade pip

# Copy the Django project  and install dependencies
COPY requirements.txt  /democracy/

# run this command to install all dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the Django project to the container
COPY . /democracy/

# Expose the Django port
EXPOSE 8000

# Make the entrypoint script executable
RUN chmod +x entrypoint.sh

# Use entrypoint to migrate
ENTRYPOINT ["./entrypoint.sh"]

# Run Django’s development server
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]