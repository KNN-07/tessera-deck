/*----------------------------------------------*/
/* TJpgDec System Configurations R0.03          */
/* Tessera Deck configuration                   */
/*----------------------------------------------*/
#ifndef TJPGDCNF_H
#define TJPGDCNF_H

/* Size of stream input buffer */
#define JD_SZBUF 512

/* Output pixel format: 0 RGB888, 1 RGB565, 2 grayscale */
#define JD_FORMAT 1

/* Output descaling: not used, every image arrives at its final size */
#define JD_USE_SCALE 0

/* Table conversion for saturation arithmetic (+1 KB code, a bit faster) */
#define JD_TBLCLIP 1

/* 1 = 32-bit barrel shifter, suitable for 32-bit MCUs */
#define JD_FASTDECODE 1

/* Espressif's optional built-in Huffman tables: not used */
#define JD_DEFAULT_HUFFMAN 0

#endif
