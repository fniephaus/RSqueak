# struct VirtualMachine* sqGetInterpreterProxy(void);

# typedef struct VirtualMachine {
#   sqInt (*minorVersion)(void);
# } VirtualMachine;

# Loading a Plugin:
#   plugin setInterpreter: proxy.
#   (plugin respondsTo: #initialiseModule) ifTrue:[plugin initialiseModule].
#   plugin perform: primitiveName asSymbol.
import inspect

from rpython.rlib.entrypoint import entrypoint
from rpython.rtyper.annlowlevel import llhelper
from rpython.rlib.exports import export_struct
from rpython.rtyper.lltypesystem.lltype import FuncType, Ptr
from rpython.rtyper.lltypesystem import lltype, rffi
from rpython.rlib.unroll import unrolling_iterable

from spyvm import error, model

sqInt = rffi.INT
sqLong = rffi.LONG
sqDouble = rffi.DOUBLE
sqIntArrayPtr = Ptr(rffi.CArray(sqInt))

major = minor = 0
functions = []

oop = object()

class ProxyFunctionFailed(error.PrimitiveFailedError):
    pass

def expose_on_virtual_machine_proxy(unwrap_spec, result_type, minor=0, major=1):
    mapping = {oop: sqInt, int: sqInt, list: sqIntArrayPtr, bool: sqInt, float: sqDouble}
    f_ptr = Ptr(FuncType([mapping[spec] for spec in unwrap_spec], mapping[result_type]))
    if minor < minor:
        minor = minor
    if major < major:
        major = major
    def decorator(func):
        len_unwrap_spec = len(unwrap_spec)
        assert (len_unwrap_spec == len(inspect.getargspec(func)[0]) + 1,
                "wrong number of arguments")
        unrolling_unwrap_spec = unrolling_iterable(enumerate(unwrap_spec))
        def wrapped(*c_arguments):
            assert len_unwrap_spec == len(c_arguments)
            args = ()
            try:
                for i, spec in unrolling_unwrap_spec:
                    c_arg = c_arguments[i]
                    if spec is oop:
                        args += (IProxy.oop_to_object(c_arg), )
                    else:
                        args += (c_arg, )
                result = func(*args)
                if result_type is oop:
                    assert isinstance(result, model.W_Object)
                    return IProxy.object_to_oop(result)
                elif result_type is list:
                    assert isinstance(result, list)
                    return IProxy.list_to_carray(result)
                elif result_type in (int, float, bool):
                    assert isinstance(result, result_type)
                    return result
                else:
                    return result
            except error.PrimitiveFailedError:
                IProxy.success_flag = False
                if mapping[result_type] is sqInt:
                    return 0
                elif mapping[result_type] is sqDouble:
                    return 0.0
                elif mapping[result_type] is sqIntArrayPtr:
                    return rffi.cast(sqIntArrayPtr, 0)
                else:
                    raise NotImplementedError(
                        "InterpreterProxy: unknown result_type %s" % (result_type, ))
        functions.append(("c_" + func.func_name, f_ptr, wrapped))
        return wrapped
    return decorator

@expose_on_virtual_machine_proxy([], int)
def minorVersion():
    return minor

@expose_on_virtual_machine_proxy([], int)
def majorVersion():
    return major

@expose_on_virtual_machine_proxy([int], int)
def pop(nItems):
    IProxy.s_frame.pop_n(nItems)
    return 0

@expose_on_virtual_machine_proxy([int, oop], int)
def popthenPush(nItems, w_object):
    s_frame = IProxy.s_frame
    s_frame.pop_n(nItems)
    s_frame.push(w_object)
    return 0

@expose_on_virtual_machine_proxy([oop], int)
def push(w_object):
    s_frame = IProxy.s_frame
    s_frame.push(w_object)
    return 0

@expose_on_virtual_machine_proxy([bool], int)
def pushBool(trueOrFalse):
    s_frame = IProxy.s_frame
    if trueOrFalse:
        s_frame.push(IProxy.interp.space.w_true)
    else:
        s_frame.push(IProxy.interp.space.w_false)
    return 0

@expose_on_virtual_machine_proxy([float], int)
def pushFloat(f):
    s_frame = IProxy.s_frame
    s_frame.push(IProxy.space.wrap_float(f))
    return 0

@expose_on_virtual_machine_proxy([int], int)
def pushInteger(n):
    s_frame = IProxy.s_frame
    s_frame.push(IProxy.space.wrap_int(n))
    return 0

@expose_on_virtual_machine_proxy([int], float)
def stackFloatValue(offset):
    s_frame = IProxy.s_frame
    f = s_frame.peek(offset)
    if isinstance(f, model.W_Float):
        return f.value
    else:
        raise ProxyFunctionFailed

@expose_on_virtual_machine_proxy([int], int)
def stackIntegerValue(offset):
    s_frame = IProxy.s_frame
    n = s_frame.peek(offset)
    return IProxy.space.unwrap_int(n)

@expose_on_virtual_machine_proxy([int], oop)
def stackObjectValue(offset):
    s_frame = IProxy.s_frame
    w_object = s_frame.peek(offset)
    if not isinstance(w_object, model.W_SmallInteger):
        return w_object
    raise ProxyFunctionFailed

@expose_on_virtual_machine_proxy([int], oop)
def stackValue(offset):
    s_frame = IProxy.s_frame
    return s_frame.peek(offset)

@expose_on_virtual_machine_proxy([oop], int)
def argumentCountOf(w_method):
    if isinstance(w_method, model.W_CompiledMethod):
        return w_method.argsize
    raise ProxyFunctionFailed

@expose_on_virtual_machine_proxy([oop], list)
def arrayValueOf(w_array):
    if w_array.is_array_object():
        raise NotImplementedError
    raise ProxyFunctionFailed

@expose_on_virtual_machine_proxy([oop], int)
def byteSizeOf(w_object):
    s_class = w_object.shadow_of_my_class(IProxy.space)
    size = s_class.instsize()
    if s_class.isvariable():
        size += w_object.primsize(IProxy.space)
    if isinstance(w_object, model.W_BytesObject):
        size *= 4
    return size

@expose_on_virtual_machine_proxy([int, oop], list)
def fetchArrayofObject(fieldIndex, w_object):
    # arrayOop := self fetchPointer: fieldIndex ofObject: objectPointer.
    # ^ self arrayValueOf: arrayOop
    w_array = w_object.fetch(IProxy.space, fieldIndex)
    if w_array.is_array_object():
        raise NotImplementedError
    raise ProxyFunctionFailed

@expose_on_virtual_machine_proxy([oop], oop)
def fetchClassOf(w_object):
    w_class = w_object.getclass(IProxy.space)
    return w_class
#     sqInt  (*fetchClassOf)(sqInt oop);
#     double (*fetchFloatofObject)(sqInt fieldIndex, sqInt objectPointer);
#     sqInt  (*fetchIntegerofObject)(sqInt fieldIndex, sqInt objectPointer);
#     sqInt  (*fetchPointerofObject)(sqInt fieldIndex, sqInt oop);
#     sqInt  (*obsoleteDontUseThisFetchWordofObject)(sqInt fieldFieldIndex, sqInt oop);
#     void  *(*firstFixedField)(sqInt oop);
#     void  *(*firstIndexableField)(sqInt oop);
#     sqInt  (*literalofMethod)(sqInt offset, sqInt methodPointer);
#     sqInt  (*literalCountOf)(sqInt methodPointer);
#     sqInt  (*methodArgumentCount)(void);
#     sqInt  (*methodPrimitiveIndex)(void);
#     sqInt  (*primitiveIndexOf)(sqInt methodPointer);
#     sqInt  (*sizeOfSTArrayFromCPrimitive)(void *cPtr);
#     sqInt  (*slotSizeOf)(sqInt oop);
#     sqInt  (*stObjectat)(sqInt array, sqInt fieldIndex);
#     sqInt  (*stObjectatput)(sqInt array, sqInt fieldIndex, sqInt value);
#     sqInt  (*stSizeOf)(sqInt oop);
#     sqInt  (*storeIntegerofObjectwithValue)(sqInt fieldIndex, sqInt oop, sqInt integer);
#     sqInt  (*storePointerofObjectwithValue)(sqInt fieldIndex, sqInt oop, sqInt valuePointer);

#     /* InterpreterProxy methodsFor: 'testing' */

#     sqInt (*isKindOf)(sqInt oop, char *aString);
#     sqInt (*isMemberOf)(sqInt oop, char *aString);
#     sqInt (*isBytes)(sqInt oop);
#     sqInt (*isFloatObject)(sqInt oop);
#     sqInt (*isIndexable)(sqInt oop);
#     sqInt (*isIntegerObject)(sqInt objectPointer);
#     sqInt (*isIntegerValue)(sqInt intValue);
#     sqInt (*isPointers)(sqInt oop);
#     sqInt (*isWeak)(sqInt oop);
#     sqInt (*isWords)(sqInt oop);
#     sqInt (*isWordsOrBytes)(sqInt oop);

#     /* InterpreterProxy methodsFor: 'converting' */

#     sqInt  (*booleanValueOf)(sqInt obj);
#     sqInt  (*checkedIntegerValueOf)(sqInt intOop);
#     sqInt  (*floatObjectOf)(double aFloat);
#     double (*floatValueOf)(sqInt oop);
#     sqInt  (*integerObjectOf)(sqInt value);
#     sqInt  (*integerValueOf)(sqInt oop);
#     sqInt  (*positive32BitIntegerFor)(sqInt integerValue);
#     sqInt  (*positive32BitValueOf)(sqInt oop);

#     /* InterpreterProxy methodsFor: 'special objects' */

#     sqInt (*characterTable)(void);
#     sqInt (*displayObject)(void);
#     sqInt (*falseObject)(void);
#     sqInt (*nilObject)(void);
#     sqInt (*trueObject)(void);

#     /* InterpreterProxy methodsFor: 'special classes' */

#     sqInt (*classArray)(void);
#     sqInt (*classBitmap)(void);
#     sqInt (*classByteArray)(void);
#     sqInt (*classCharacter)(void);
#     sqInt (*classFloat)(void);
#     sqInt (*classLargePositiveInteger)(void);
#     sqInt (*classPoint)(void);
#     sqInt (*classSemaphore)(void);
#     sqInt (*classSmallInteger)(void);
#     sqInt (*classString)(void);

#     /* InterpreterProxy methodsFor: 'instance creation' */

#     sqInt (*clone)(sqInt oop);
#     sqInt (*instantiateClassindexableSize)(sqInt classPointer, sqInt size);
#     sqInt (*makePointwithxValueyValue)(sqInt xValue, sqInt yValue);
#     sqInt (*popRemappableOop)(void);
#     sqInt (*pushRemappableOop)(sqInt oop);

#     /* InterpreterProxy methodsFor: 'other' */

#     sqInt (*becomewith)(sqInt array1, sqInt array2);
#     sqInt (*byteSwapped)(sqInt w);
#     sqInt (*failed)(void);
#     sqInt (*fullDisplayUpdate)(void);
#     sqInt (*fullGC)(void);
#     sqInt (*incrementalGC)(void);
#     sqInt (*primitiveFail)(void);
#     sqInt (*showDisplayBitsLeftTopRightBottom)(sqInt aForm, sqInt l, sqInt t, sqInt r, sqInt b);
#     sqInt (*signalSemaphoreWithIndex)(sqInt semaIndex);
#     sqInt (*success)(sqInt aBoolean);
#     sqInt (*superclassOf)(sqInt classPointer);

#     /* InterpreterProxy methodsFor: 'compiler' */

#     CompilerHook *(*compilerHookVector)(void);
#     sqInt          (*setCompilerInitialized)(sqInt initFlag);

# #if VM_PROXY_MINOR > 1

#     /* InterpreterProxy methodsFor: 'BitBlt support' */

#     sqInt (*loadBitBltFrom)(sqInt bbOop);
#     sqInt (*copyBits)(void);
#     sqInt (*copyBitsFromtoat)(sqInt leftX, sqInt rightX, sqInt yValue);

# #endif

# #if VM_PROXY_MINOR > 2

#     sqInt (*classLargeNegativeInteger)(void);
#     sqInt (*signed32BitIntegerFor)(sqInt integerValue);
#     sqInt (*signed32BitValueOf)(sqInt oop);
#     sqInt (*includesBehaviorThatOf)(sqInt aClass, sqInt aSuperClass);
#     sqInt (*primitiveMethod)(void);

#     /* InterpreterProxy methodsFor: 'FFI support' */

#     sqInt (*classExternalAddress)(void);
#     sqInt (*classExternalData)(void);
#     sqInt (*classExternalFunction)(void);
#     sqInt (*classExternalLibrary)(void);
#     sqInt (*classExternalStructure)(void);
#     sqInt (*ioLoadModuleOfLength)(sqInt modIndex, sqInt modLength);
#     sqInt (*ioLoadSymbolOfLengthFromModule)(sqInt fnIndex, sqInt fnLength, sqInt handle);
#     sqInt (*isInMemory)(sqInt address);

# #endif

# #if VM_PROXY_MINOR > 3

#     void *(*ioLoadFunctionFrom)(char *fnName, char *modName);
#     sqInt (*ioMicroMSecs)(void);

# #endif

# #if VM_PROXY_MINOR > 4

# #  if !defined(sqLong)
# #   if _MSC_VER
# #     define sqLong __int64
# #     define usqLong unsigned __int64
# #   else
# #     define sqLong long long
# #     define usqLong unsigned long long
# #   endif
# #  endif

#     sqInt  (*positive64BitIntegerFor)(sqLong integerValue);
#     sqLong (*positive64BitValueOf)(sqInt oop);
#     sqInt  (*signed64BitIntegerFor)(sqLong integerValue);
#     sqLong (*signed64BitValueOf)(sqInt oop);

# #endif

# #if VM_PROXY_MINOR > 5
#     sqInt (*isArray)(sqInt oop);
#     sqInt (*forceInterruptCheck)(void);
# #endif

# #if VM_PROXY_MINOR > 6
#     sqInt  (*fetchLong32ofObject)(sqInt fieldFieldIndex, sqInt oop);
#     sqInt  (*getThisSessionID)(void);
#     sqInt     (*ioFilenamefromStringofLengthresolveAliases)(char* aCharBuffer, char* filenameIndex, sqInt filenameLength, sqInt resolveFlag);
#     sqInt  (*vmEndianness)(void);
# #endif

# #if VM_PROXY_MINOR > 7
#   /* New methods for proxy version 1.8 */

#   /* callbackEnter: Re-enter the interpreter loop for a callback.
#      Arguments:
#        callbackID: Pointer to a location receiving the callback ID
#                    used in callbackLeave
#      Returns: True if successful, false otherwise */
#   sqInt (*callbackEnter)(sqInt *callbackID);

#   /* callbackLeave: Leave the interpreter from a previous callback
#      Arguments:
#        callbackID: The ID of the callback received from callbackEnter()
#      Returns: True if succcessful, false otherwise. */
#   sqInt (*callbackLeave)(sqInt  callbackID);

#   /* addGCRoot: Add a variable location to the garbage collector.
#      The contents of the variable location will be updated accordingly.
#      Arguments:
#        varLoc: Pointer to the variable location
#      Returns: True if successful, false otherwise. */
#   sqInt (*addGCRoot)(sqInt *varLoc);

#   /* removeGCRoot: Remove a variable location from the garbage collector.
#      Arguments:
#        varLoc: Pointer to the variable location
#      Returns: True if successful, false otherwise.
#   */
#   sqInt (*removeGCRoot)(sqInt *varLoc);
# #endif

# #if VM_PROXY_MINOR > 8
#     /* See interp.h and above for standard error codes. */
#     sqInt  (*primitiveFailFor)(sqInt code);
#     void (*(*setInterruptCheckChain)(void (*aFunction)(void)))();
#     sqInt  (*classAlien)(void);
#     sqInt  (*classUnsafeAlien)(void);
#     sqInt  (*sendInvokeCallbackStackRegistersJmpbuf)(sqInt thunkPtrAsInt, sqInt stackPtrAsInt, sqInt regsPtrAsInt, sqInt jmpBufPtrAsInt);
#     sqInt  (*reestablishContextPriorToCallback)(sqInt callbackContext);
#     sqInt *(*getStackPointer)(void);
#     sqInt  (*isOopImmutable)(sqInt oop);
#     sqInt  (*isOopMutable)(sqInt oop);
# #endif

# #if VM_PROXY_MINOR > 9
#   sqInt  (*methodArg)  (sqInt index);
#   sqInt  (*objectArg)  (sqInt index);
#   sqInt  (*integerArg) (sqInt index);
#   double (*floatArg)   (sqInt index);
#   sqInt  (*methodReturnValue) (sqInt oop);
#   sqInt  (*topRemappableOop)  (void);
# #endif

# #if VM_PROXY_MINOR > 10
# # define DisownVMLockOutFullGC 1
#   sqInt (*disownVM)(sqInt flags);
#   sqInt (*ownVM)   (sqInt threadIdAndFlags);
#   void  (*addHighPriorityTickee)(void (*ticker)(void), unsigned periodms);
#   void  (*addSynchronousTickee)(void (*ticker)(void), unsigned periodms, unsigned roundms);
#   usqLong (*utcMicroseconds)(void);
#   sqInt (*tenuringIncrementalGC)(void);
#   sqInt (*isYoung) (sqInt anOop);
#   sqInt (*isKindOfClass)(sqInt oop, sqInt aClass);
#   sqInt (*primitiveErrorTable)(void);
#   sqInt (*primitiveFailureCode)(void);
#   sqInt (*instanceSizeOf)(sqInt aClass);
# #endif

# #if VM_PROXY_MINOR > 11
# /* VMCallbackContext opaque type avoids all including setjmp.h & vmCallback.h */
#   sqInt (*sendInvokeCallbackContext)(vmccp);
#   sqInt (*returnAsThroughCallbackContext)(int, vmccp, sqInt);
#   long  (*signedMachineIntegerValueOf)(sqInt);
#   long  (*stackSignedMachineIntegerValue)(sqInt);
#   unsigned long  (*positiveMachineIntegerValueOf)(sqInt);
#   unsigned long  (*stackPositiveMachineIntegerValue)(sqInt);
#   sqInt  (*getInterruptPending)(void);
#   char  *(*cStringOrNullFor)(sqInt);
#   void  *(*startOfAlienData)(sqInt);
#   usqInt (*sizeOfAlienData)(sqInt);
#   sqInt  (*signalNoResume)(sqInt);
# #endif

# ##############################################################################

VirtualMachine = lltype.Struct("VirtualMachine",
        *map(lambda x: (x[0], x[1]), functions),
        hints={'c_name': 'VirtualMachine'})
VMPtr = Ptr(VirtualMachine)

proxy_functions = unrolling_iterable(functions)

@entrypoint('main', [], c_name='sqGetInterpreterProxy')
def sqGetInterpreterProxy():
    if not IProxy.vm_initialized:
        vm_proxy = lltype.malloc(VirtualMachine, flavor='raw')
        for func_name, signature, func in proxy_functions:
            setattr(vm_proxy, func_name, llhelper(signature, func))
        IProxy.vm_proxy = vm_proxy
        IProxy.vm_initialized = True
    return IProxy.vm_proxy

# rffi.llexternal is supposed to represent c-functions.

class _InterpreterProxy(object):
    _immutable_fields_ = ['vm_initialized?']

    def __init__(self):
        self.vm_proxy = lltype.nullptr(VMPtr.TO)
        self.vm_initialized = False
        self.reset()

    def reset(self):
        self.interp = None
        self.s_frame = None
        self.argcount = 0
        self.s_method = None
        self.success_flag = True

    def call(self, signature, interp, s_frame, argcount, s_method):
        self.interp = interp
        self.s_frame = s_frame
        self.argcount = argcount
        self.s_method = s_method
        self.space = interp.space
        try:
            print "Hello World..."
            raise error.Exit("External Call")
        finally:
            self.reset()

    def oop_to_object(self, oop):
        return self.interp.space.w_nil

    def object_to_oop(self, oop):
        return 0

IProxy = _InterpreterProxy()
