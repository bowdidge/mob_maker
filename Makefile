all: test

test:
	python3 -m unittest discover -p "*_test.py"

clean:
	rm -f *.pyc
