import inspect

def debug(msg):
    frame = inspect.currentframe().f_back
    print(f"{frame.f_code.co_name}:{frame.f_lineno} - {msg}")



def foo():
    debug("")

foo()