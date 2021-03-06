import os

from rsqueakvm import constants, model, wrapper, display, storage
from rsqueakvm.util.version import Version
from rsqueakvm.error import WrappingError
from rsqueakvm.constants import SYSTEM_ATTRIBUTE_IMAGE_NAME_INDEX, SYSTEM_ATTRIBUTE_IMAGE_ARGS_INDEX
from rpython.rlib import jit, rpath
from rpython.rlib.objectmodel import instantiate, specialize, import_from_mixin, we_are_translated
from rpython.rlib.rarithmetic import intmask, r_uint, r_uint32, int_between, r_int64, r_ulonglong, is_valid_int, r_longlonglong


class ConstantMixin(object):
    """Mixin for constant values that can be edited, but will be promoted
    to a constant when jitting."""
    _immutable_fields_ = ["value?"]

    def __init__(self, initial_value=None):
        if initial_value is None:
            initial_value = self.default_value
        self.value = initial_value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value

class ConstantFlag(object):
    import_from_mixin(ConstantMixin)
    default_value = False
    def is_set(self):
        return self.get()
    def activate(self):
        self.set(True)
    def deactivate(self):
        self.set(False)

class ConstantString(object):
    import_from_mixin(ConstantMixin)
    default_value = ""

class ConstantObject(object):
    import_from_mixin(ConstantMixin)
    default_value = None

class ConstantVersion(object):
    import_from_mixin(ConstantMixin)
    default_value = Version()

def empty_object():
    return instantiate(model.W_PointersObject)

class ForceHeadless(object):
    def __init__(self, space):
        self.space = space
        self.was_headfull = not space.headless.is_set()
    def __enter__(self):
        if self.was_headfull:
            self.space.headless.activate()
    def __exit__(self, type, value, traceback):
        if self.was_headfull:
            self.space.headless.deactivate()

class ObjSpace(object):
    _immutable_fields_ = ['objtable']

    def __init__(self):
        # This is a hack; see compile_code() in targetrsqueak.py
        self.suppress_process_switch = ConstantFlag()
        self.run_spy_hacks = ConstantFlag()
        self.headless = ConstantFlag()
        self.highdpi = ConstantFlag(True)
        self.use_plugins = ConstantFlag()
        self.omit_printing_raw_bytes = ConstantFlag()
        self.image_loaded = ConstantFlag()
        self.is_spur = ConstantFlag()
        self.uses_block_contexts = ConstantFlag()
        self.simulate_numeric_primitives = ConstantFlag()

        self.classtable = {}
        self.objtable = {}
        self.system_attributes = {}
        self._system_attribute_version = ConstantVersion()
        self._executable_path = ConstantString()
        self.title = ConstantString()
        self.altf4quit = ConstantFlag()
        self._display = ConstantObject()

        # Create the nil object.
        # Circumvent the constructor because nil is already referenced there.
        w_nil = empty_object()
        self.add_bootstrap_object("w_nil", w_nil)

        self.strategy_factory = storage.StrategyFactory(self)
        self.make_bootstrap_classes()
        self.make_bootstrap_objects()

    def runtime_setup(self, exepath, argv, image_name, image_args_idx):
        fullpath = exepath
        self._executable_path.set(fullpath)
        for i in range(image_args_idx, len(argv)):
            self.set_system_attribute(SYSTEM_ATTRIBUTE_IMAGE_ARGS_INDEX + i - image_args_idx, argv[i])
        self.set_system_attribute(SYSTEM_ATTRIBUTE_IMAGE_NAME_INDEX, image_name)
        self.image_loaded.activate()
        self.init_system_attributes(argv)
        from rpython.rlib.rsocket import rsocket_startup
        rsocket_startup()

    def init_system_attributes(self, argv):
        for i in xrange(1, len(argv)):
            self.set_system_attribute(-i, argv[i])
        import platform
        from targetrsqueak import VERSION, BUILD_DATE
        self.set_system_attribute(0, self._executable_path.get())
        self.set_system_attribute(1001, platform.system())    # operating system
        self.set_system_attribute(1002, platform.version())   # operating system version
        self.set_system_attribute(1003, platform.processor())  # platform's processor type
        self.set_system_attribute(1004, VERSION)
        self.set_system_attribute(1006, BUILD_DATE)
        self.set_system_attribute(1007, "rsqueak")            # interpreter class (invented for Cog)

    def get_system_attribute(self, idx):
        return self._pure_get_system_attribute(idx, self._system_attribute_version.get())

    @jit.elidable
    def _pure_get_system_attribute(self, idx, version):
        return self.system_attributes[idx]

    def set_system_attribute(self, idx, value):
        self.system_attributes[idx] = value
        self._system_attribute_version.set(Version())

    def populate_special_objects(self, specials):
        for name, idx in constants.objects_in_special_object_table.items():
            name = "w_" + name
            if not name in self.objtable or not self.objtable[name]:
                try:
                    self.objtable[name] = specials[idx]
                except IndexError:
                    # if it's not yet in the table, the interpreter has to fill the gap later in populate_remaining_special_objects
                    self.objtable[name] = None
        self.classtable["w_Metaclass"] = self.w_SmallInteger.getclass(self).getclass(self)

    def add_bootstrap_class(self, name, cls):
        self.classtable[name] = cls
        setattr(self, name, cls)

    def make_bootstrap_classes(self):
        names = [ "w_" + name for name in constants.classes_in_special_object_table.keys() ]
        for name in names:
            cls = empty_object()
            self.add_bootstrap_class(name, cls)

    def add_bootstrap_object(self, name, obj):
        self.objtable[name] = obj
        setattr(self, name, obj)

    def make_bootstrap_object(self, name):
        obj = empty_object()
        self.add_bootstrap_object(name, obj)

    def make_bootstrap_objects(self):
        self.make_bootstrap_object("w_true")
        self.make_bootstrap_object("w_false")
        self.make_bootstrap_object("w_special_selectors")
        self.add_bootstrap_object("w_minus_one", model.W_SmallInteger(-1))
        self.add_bootstrap_object("w_zero", model.W_SmallInteger(0))
        self.add_bootstrap_object("w_one", model.W_SmallInteger(1))
        self.add_bootstrap_object("w_two", model.W_SmallInteger(2))

        # Certain special objects are already created. The rest will be
        # populated when the image is loaded, but prepare empty slots for them.
        for name in constants.objects_in_special_object_table:
            name = "w_" + name
            if not name in self.objtable:
                self.add_bootstrap_object(name, None)

    @jit.elidable
    def special_object(self, which):
        return self.objtable[which]

    # ============= Methods for wrapping and unwrapping stuff =============

    @specialize.argtype(1)
    def wrap_int(self, val):
        if isinstance(val, r_int64) and not is_valid_int(val):
            if val > 0 and val <= r_int64(constants.U_MAXINT):
                return self.wrap_positive_wordsize_int(intmask(val))
            else:
                raise WrappingError
        elif isinstance(val, r_uint) or isinstance(val, r_uint32):
            return self.wrap_positive_wordsize_int(intmask(val))
        elif not is_valid_int(val):
            raise WrappingError
        # we don't do tagging
        return model.W_SmallInteger(intmask(val))

    def wrap_uint(self, val):
        if val < 0:
            raise WrappingError("negative integer")
        else:
            return self.wrap_positive_wordsize_int(intmask(val))

    def wrap_positive_wordsize_int(self, val):
        # This will always return a positive value.
        from rpython.rlib.objectmodel import we_are_translated
        if not we_are_translated() and val < 0:
            print "WARNING: wrap_positive_32bit_int casts %d to 32bit unsigned" % val
        if int_between(0, val, constants.MAXINT):
            return model.W_SmallInteger(val)
        else:
            return model.W_LargePositiveInteger1Word(val)

    @jit.unroll_safe
    @specialize.arg(2)
    def wrap_large_number(self, val, w_class):
        # import pdb; pdb.set_trace()
        assert isinstance(val, r_ulonglong)
        inst_size = self._number_bytesize(val)
        w_result = w_class.as_class_get_shadow(self).new(inst_size)
        for i in range(inst_size):
            byte_value = (val >> (i * 8)) & 255
            w_result.setchar(i, chr(byte_value))
        return w_result

    @jit.unroll_safe
    def _number_bytesize(self, val):
        assert val != 0
        sz = 0
        while val != 0:
            sz += 1
            val = val >> 8
        return sz

    @specialize.argtype(1)
    def wrap_ulonglong(self, val):
        assert val > 0 and not is_valid_int(val)
        r_val = r_ulonglong(val)
        w_class = self.w_LargePositiveInteger
        return self.wrap_large_number(r_val, w_class)

    @specialize.argtype(1)
    def wrap_nlonglong(self, val):
        if self.w_LargeNegativeInteger is None:
            raise WrappingError
        assert val < 0 and not is_valid_int(val)
        try:
            r_val = r_ulonglong(-val)
        except OverflowError:
            # this is a negative max-bit r_int64, mask by simple coercion
            r_val = r_ulonglong(val)
        w_class = self.w_LargeNegativeInteger
        return self.wrap_large_number(r_val, w_class)

    def wrap_long_untranslated(self, val):
        "NOT_RPYTHON"
        if val > 0:
            w_class = self.w_LargePositiveInteger
        elif  val < 0:
            w_class = self.w_LargeNegativeInteger
            val = -val
        else:
            raise WrappingError
        inst_size = self._number_bytesize(val)
        w_result = w_class.as_class_get_shadow(self).new(inst_size)
        for i in range(inst_size):
            byte_value = (val >> (i * 8)) & 255
            w_result.setchar(i, chr(byte_value))
        return w_result

    @specialize.argtype(1)
    def wrap_longlong(self, val):
        if not we_are_translated():
            "Tests only"
            if isinstance(val, long) and not isinstance(val, r_int64):
                return self.wrap_long_untranslated(val)

        if not is_valid_int(val):
            if isinstance(val, r_ulonglong):
                return self.wrap_ulonglong(val)
            elif isinstance(val, r_int64):
                if val > 0:
                    if constants.IS_64BIT:
                        if not val <= r_longlonglong(constants.U_MAXINT):
                            # on 64bit, U_MAXINT must be wrapped in an unsigned longlonglong
                            return self.wrap_ulonglong(val)
                    else:
                        if not val <= r_int64(constants.U_MAXINT):
                            return self.wrap_ulonglong(val)
                elif val < 0:
                    return self.wrap_nlonglong(val)
        # handles the rest and raises if necessary
        return self.wrap_int(val)

    def wrap_float(self, i):
        return model.W_Float(i)

    def wrap_string(self, string):
        w_inst = self.w_String.as_class_get_shadow(self).new(len(string))
        for i in range(len(string)):
            w_inst.setchar(i, string[i])
        return w_inst

    def wrap_char(self, c):
        # return self.w_charactertable.fetch(self, ord(c))
        return model.W_Character(ord(c))

    def wrap_bool(self, b):
        if b:
            return self.w_true
        else:
            return self.w_false

    def wrap_list(self, lst_w):
        """
        Converts a Python list of wrapped objects into
        a wrapped smalltalk array
        """
        lstlen = len(lst_w)
        res = self.w_Array.as_class_get_shadow(self).new(lstlen)
        for i in range(lstlen):
            res.atput0(self, i, lst_w[i])
        return res

    @jit.unroll_safe
    def wrap_list_unroll_safe(self, lst_w):
        lstlen = len(lst_w)
        res = self.w_Array.as_class_get_shadow(self).new(lstlen)
        for i in range(lstlen):
            res.atput0(self, i, lst_w[i])
        return res

    def unwrap_int(self, w_value):
        return w_value.unwrap_int(self)

    def unwrap_uint(self, w_value):
        return w_value.unwrap_uint(self)

    def unwrap_positive_wordsize_int(self, w_value):
        return w_value.unwrap_positive_wordsize_int(self)

    def unwrap_longlong(self, w_value):
        return w_value.unwrap_longlong(self)

    def unwrap_char_as_byte(self, w_char):
        return w_char.unwrap_char_as_byte(self)

    def unwrap_float(self, w_v):
        return w_v.unwrap_float(self)

    def unwrap_array(self, w_array):
        return w_array.unwrap_array(self)

    def unwrap_string(self, w_object):
        return w_object.unwrap_string(self)

    # ============= Access to static information =============

    @specialize.arg(1)
    def get_special_selector(self, selector):
        i0 = constants.find_selectorindex(selector)
        self.w_special_selectors.as_cached_object_get_shadow(self)
        return self.w_special_selectors.fetch(self, i0)

    def executable_path(self):
        return self._executable_path.get()

    def display(self):
        disp = self._display.get()
        if disp is None:
            # Create lazy to allow headless execution.
            title = self.title.get()
            if len(title) == 0:
                title = self.get_system_attribute(SYSTEM_ATTRIBUTE_IMAGE_NAME_INDEX)
            disp = display.SDLDisplay(
                title,
                self.highdpi.is_set(),
                self.altf4quit.is_set()
            )
            self._display.set(disp)
        return jit.promote(disp)

    # ============= Other Methods =============

    def _freeze_(self):
        return True

    @jit.unroll_safe
    def newClosure(self, w_outer_ctxt, pc, numArgs, copiedValues):
        assert isinstance(w_outer_ctxt, model.W_PointersObject)
        pc_with_bytecodeoffset = pc + w_outer_ctxt.as_context_get_shadow(self).w_method().bytecodeoffset() + 1
        BlockClosureShadow = self.w_BlockClosure.as_class_get_shadow(self)
        numCopied = len(copiedValues)
        w_closure = BlockClosureShadow.new(numCopied)
        closure = wrapper.BlockClosureWrapper(self, w_closure)
        closure.store_outerContext(w_outer_ctxt)
        closure.store_startpc(pc_with_bytecodeoffset)
        closure.store_numArgs(numArgs)
        for i0 in range(numCopied):
            closure.atput0(i0, copiedValues[i0])
        return w_closure
